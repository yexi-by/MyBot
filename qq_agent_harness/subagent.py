"""内存态轻量子任务委派（Subagent）：继承当前会话策略，隔离繁重探索过程，不直接对外发言。"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace

from pydantic import Field

from .configuration import ConfigStore
from .contracts import (
    JsonObject,
    JsonValue,
    Model,
    OrderedSet,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
)
from .providers import CompactionError, ContextLimitError, ModelSession, ProviderError
from .registry import ToolRegistry

CATEGORY_LABELS = {"subagent": "子任务委派"}
MAX_SUBAGENT_STEPS = 24

SUBAGENT_SYSTEM_PREFIX = """你是主会话派生的内部执行子智能体，负责在独立上下文中完成指定的繁重分析、检索、媒体处理或工作区计算任务。
- 你没有直接向 QQ 发送消息或执行群管写入的工具；请专注于调查、运行验证并产出清晰结论。
- 工作区中单次命令临时文件写 $TMPDIR，跨步骤临时素材放 .scratch/，需要交回主会话复用的文件或生成媒体请明确记录其 workspace_path 或 generated_id。
- 任务完成后，直接输出结构清晰、事实准确的总结报告供主会话使用；未确认的结果严禁臆测。"""


class SubagentInput(Model):
    task: str = Field(
        min_length=1,
        max_length=8000,
        description="委派给子智能体的明确任务目标、输入材料路径/ID 与期望返回内容",
    )
    context_hint: str = Field(
        default="",
        max_length=4000,
        description="可选：补充给子智能体的简短会话背景说明",
    )


def _allowed_for_subagent(tool: ToolDefinition) -> bool:
    """子智能体仅可使用当前群已授权的工作区、MCP、技能、媒体生成及只读 QQ 查询工具。"""
    name = tool.name
    if name.startswith("subagent__") or name.startswith("timer__"):
        return False
    if name == "qq__send_message":
        return False
    if name.startswith("qq__"):
        return tool.concurrency_safe or name == "qq__save_media_to_workspace"
    return True


def _subagent_definitions(registry: ToolRegistry, context: ToolContext) -> list[JsonObject]:
    result: list[JsonObject] = []
    for name in sorted(registry.tools):
        tool = registry.tools[name]
        if tool.deferred or not _allowed_for_subagent(tool):
            continue
        if not registry.permitted(context.scope, tool)[0]:
            continue
        result.append(registry.definition(tool, context.scope))
    seen: set[str] = set()
    for name in context.discovered_tools:
        if name in seen:
            continue
        seen.add(name)
        tool = registry.tools.get(name)
        if (
            tool is not None
            and tool.deferred
            and _allowed_for_subagent(tool)
            and registry.permitted(context.scope, tool)[0]
        ):
            result.append(registry.definition(tool, context.scope))
    return result


def build_tools(
    get_store: Callable[[], ConfigStore],
    registry: ToolRegistry,
    session_factory: Callable[..., ModelSession] = ModelSession,
) -> list[ToolDefinition]:
    async def run_subagent(context: ToolContext, request: SubagentInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        store = get_store()
        endpoint = store.settings.session_endpoint(context.scope)
        if endpoint is None:
            return ToolResult.failure("当前会话未配置有效模型", "PermissionDenied")

        child_context = replace(
            context,
            turn_id=f"{context.turn_id}:sub",
            discovered_tools=OrderedSet(context.discovered_tools),
        )
        prompt_payload: JsonObject = {
            "来源会话": context.scope.model_dump(mode="json"),
            "委派任务": request.task,
        }
        if request.context_hint.strip():
            prompt_payload["补充背景"] = request.context_hint.strip()

        session = session_factory(
            endpoint,
            SUBAGENT_SYSTEM_PREFIX,
            json.dumps(prompt_payload, ensure_ascii=False),
        )
        repeat_key: tuple[str, str] | None = None
        repeat_count = 0
        steps = 0
        before_generated = set(context.generated_media.keys())
        try:
            while steps < MAX_SUBAGENT_STEPS:
                if context.cancelled():
                    return ToolResult(status="cancelled", message="当前轮次已停止")
                if denied := context.recheck():
                    return denied
                steps += 1
                defs = _subagent_definitions(registry, child_context)
                reply = await session.step(defs)
                if not reply.calls:
                    new_media: list[JsonValue] = [
                        gid for gid in context.generated_media if gid not in before_generated
                    ]
                    payload: JsonObject = {
                        "report": reply.text.strip(),
                        "steps": steps,
                        "generated_ids": new_media,
                    }
                    return ToolResult.success(payload)

                # 分批执行：连续只读安全工具并发，其余工具串行。
                idx = 0
                calls = reply.calls
                while idx < len(calls):
                    call = calls[idx]
                    tool_def = registry.tools.get(call.name)
                    if call.invalid:
                        session.result(
                            call,
                            ToolResult.failure("工具参数不是有效 JSON 对象", "InvalidArguments"),
                        )
                        idx += 1
                        continue
                    if tool_def is None or not _allowed_for_subagent(tool_def):
                        session.result(
                            call,
                            ToolResult.failure(
                                "子智能体不允许调用该工具（对外发送或群管请交回主会话执行）",
                                "PermissionDenied",
                            ),
                        )
                        idx += 1
                        continue

                    if tool_def.concurrency_safe:
                        batch = [call]
                        idx += 1
                        while idx < len(calls) and len(batch) < 8:
                            nxt = calls[idx]
                            ntool = registry.tools.get(nxt.name)
                            if (
                                nxt.invalid
                                or ntool is None
                                or not _allowed_for_subagent(ntool)
                                or not ntool.concurrency_safe
                            ):
                                break
                            batch.append(nxt)
                            idx += 1
                        results = await asyncio.gather(
                            *(registry.execute(child_context, c.name, c.arguments) for c in batch)
                        )
                        for c, r in zip(batch, results, strict=True):
                            session.result(c, r)
                        continue

                    sig = (
                        call.name,
                        json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
                    )
                    if sig == repeat_key:
                        repeat_count += 1
                    else:
                        repeat_key, repeat_count = sig, 1
                    if repeat_count >= 6:
                        res = ToolResult.failure(
                            "子智能体连续重复相同工具调用无进展，已拦截；请换方案或直接汇总已有发现。",
                            "RepeatedToolCallBlocked",
                        )
                    else:
                        res = await registry.execute(child_context, call.name, call.arguments)
                    session.result(call, res)
                    if repeat_count == 3:
                        session.add_notice(
                            "循环提醒：请勿重复完全相同的工具调用，分析已有结果或更换思路。"
                        )
                    idx += 1

            return ToolResult.failure(
                f"子智能体达到最大步数上限（{MAX_SUBAGENT_STEPS} 步）仍未收敛，请缩小任务范围",
                "SubagentStepLimit",
            )
        except asyncio.CancelledError:
            raise
        except (ContextLimitError, CompactionError, ProviderError) as exc:
            return ToolResult.failure(f"子智能体模型请求失败：{exc}", "SubagentModelError")
        finally:
            await session.close()

    return [
        ToolDefinition(
            "subagent__run",
            (
                "派生一个独立上下文的子智能体去执行繁重的多步调查、长文档阅读、MCP 批量检索或工作区计算，"
                "仅将精炼结论和生成的资源 ID 带回当前会话，避免大量中间输出污染群聊上下文。"
            ),
            "subagent",
            model_binding(SubagentInput, run_subagent),
            title="委派子任务",
        )
    ]
