"""异步子智能体（Subagent）完整回归：后台非阻塞、挂起与 @ 插队、监管纠偏/续跑、看门狗/收尾、工作区隔离与控制台 API。"""

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import tomlkit
from fastapi.testclient import TestClient
from test_harness_core import Empty, FakeHub, make_store
from test_napcat import Bot
from test_provider_context import answer

from qq_agent_harness.contracts import (
    ConversationScope,
    GeneratedMedia,
    JsonObject,
    Message,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    json_binding,
    model_binding,
    remember_generated,
    schema,
)
from qq_agent_harness.prompts import SILENT_RESPONSE
from qq_agent_harness.providers import ModelSession
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.runtime import AgentRuntime, SessionBusy
from qq_agent_harness.settings import WorkspaceSettings
from qq_agent_harness.skills import build_tools as skill_tools
from qq_agent_harness.subagent import (
    SpawnSubagentInput,
    SubagentManager,
)
from qq_agent_harness.subagent import (
    build_tools as subagent_tools,
)
from qq_agent_harness.web import create_app
from qq_agent_harness.workspace import WorkspaceService
from qq_agent_harness.workspace import build_tools as workspace_tools
from qq_agent_harness.workspace.executor import RunningProcess

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="
)


def _chat_tool_call(
    call_id: str, name: str, args: JsonObject, *, reasoning: str = ""
) -> JsonObject:
    msg: JsonObject = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        ],
    }
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"finish_reason": "tool_calls", "message": msg}]}


class _TagArg(Model):
    tag: str


class _StepArg(Model):
    n: int


async def _wait_until(predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("等待条件超时")
        await asyncio.sleep(0.01)


async def test_async_spawn_suspend_interrupt_by_at_and_auto_wake(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.groups["a"].policy.categories["subagent"] = True
    store.settings.groups["a"].idle_compaction.enabled = True
    store.settings.groups["a"].idle_compaction.after_minutes = 1
    store.settings.groups["a"].idle_compaction.min_tokens = 1

    registry = ToolRegistry(lambda: store.settings)
    sub_entered = asyncio.Event()
    release_sub = asyncio.Event()
    sent_texts: list[str] = []

    async def send_msg(ctx: ToolContext, args: _TagArg) -> ToolResult:
        sent_texts.append(args.tag)
        ctx.sent_messages.append(
            {"action": "send_group_msg", "status": "ok", "data": {"message_id": args.tag}}
        )
        return ToolResult.success({"sent": args.tag})

    async def slow_work(ctx: ToolContext, _args: JsonObject) -> ToolResult:
        sub_entered.set()
        await release_sub.wait()
        remember_generated(ctx.generated_media, GeneratedMedia("gen-sub-1", PNG_BYTES, "image/png"))
        return ToolResult.success(
            {"finding": "LinguaGacha 使用 VLM + 掩码擦除", "generated_id": "gen-sub-1"}
        )

    registry.add(
        ToolDefinition("qq__send_message", "发送", "messages", model_binding(_TagArg, send_msg))
    )
    registry.add(
        ToolDefinition(
            "work__analyze", "慢分析", "messages", json_binding(schema(Empty), slow_work)
        )
    )

    main_requests: list[str] = []
    sub_steps = 0
    main_steps = 0

    def transport_fn(request: httpx.Request) -> httpx.Response:
        nonlocal main_steps, sub_steps
        body = json.loads(request.content)
        sys_text = str(body["messages"][0]["content"])
        if "后台执行子智能体" in sys_text:
            sub_steps += 1
            if sub_steps == 1:
                return httpx.Response(200, json=_chat_tool_call("sub-c1", "work__analyze", {}))
            return httpx.Response(
                200, json=answer("chat_completions", "源码调研结论：VLM 识别 + 传统擦除")
            )

        main_steps += 1
        serialized = json.dumps(body, ensure_ascii=False)
        main_requests.append(serialized)
        if main_steps == 1:
            return httpx.Response(
                200,
                json=_chat_tool_call(
                    "m-spawn",
                    "subagent__spawn",
                    {"title": "分析 LinguaGacha", "task": "分析漫画翻译源码"},
                ),
            )
        if main_steps == 2:
            # 派发后先告诉群友一声，然后结束本轮挂起
            return httpx.Response(
                200, json=_chat_tool_call("m-ack", "qq__send_message", {"tag": "已开始后台分析"})
            )
        if main_steps == 3:
            return httpx.Response(200, json=answer("chat_completions", "已挂起等待子代理。"))
        if main_steps == 4:
            # 挂起期间被另一个人 @ 唤醒：先回复对方
            return httpx.Response(
                200, json=_chat_tool_call("m-at", "qq__send_message", {"tag": "先回复插队的人"})
            )
        if main_steps == 5:
            return httpx.Response(
                200, json=answer("chat_completions", "已回复插队群友，继续挂起。")
            )
        if main_steps == 6:
            # 子代理完成后自动唤醒主代理
            return httpx.Response(
                200, json=_chat_tool_call("m-final", "qq__send_message", {"tag": "调研完成汇报"})
            )
        return httpx.Response(200, json=answer("chat_completions", "已汇报最终结果。"))

    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_fn)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        manager = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda *args: ModelSession(*args, client=client),
            on_wake=runtime.wake_subagent,
            on_record=runtime.record,
            on_media=runtime.attach_generated_media,
            get_parent_session_id=runtime.session_id_for,
        )
        runtime.subagents = manager
        for tool in subagent_tools(manager):
            registry.add(tool)
        try:
            await runtime.receive(
                Message(
                    scope=scope,
                    message_id="m1",
                    sender_id="u1",
                    segments=[
                        {"type": "at", "data": {"qq": "bot"}},
                        {"type": "text", "data": {"text": "查源码"}},
                    ],
                )
            )
            await asyncio.wait_for(sub_entered.wait(), 2.0)
            # 主代理第 1 轮很快结束挂起，释放 worker，但子代理仍在后台活着！
            await _wait_until(lambda: scope.key not in runtime.workers)
            assert manager.has_active(scope.key) is True
            assert sent_texts == ["已开始后台分析"]

            # 挂起等子代理期间不能触发闲时压缩！
            assert runtime.compact_idle_sessions(now=time.time() + 3600) == []

            # 挂起期间另一个人 @ 机器人，主代理立即响应且 action_state 中可见活跃子代理！
            await runtime.receive(
                Message(
                    scope=scope,
                    message_id="m2",
                    sender_id="u2",
                    segments=[
                        {"type": "at", "data": {"qq": "bot"}},
                        {"type": "text", "data": {"text": "帮我看看别的"}},
                    ],
                )
            )
            await _wait_until(lambda: len(sent_texts) == 2 and scope.key not in runtime.workers)
            assert sent_texts == ["已开始后台分析", "先回复插队的人"]
            assert "active_subagents" in main_requests[3]
            assert "分析 LinguaGacha" in main_requests[3]
            assert manager.has_active(scope.key) is True

            # 停止主会话单轮（若在忙或已空闲）绝不会杀死后台运行中的子代理！
            await runtime.cancel(scope.key)
            assert manager.has_active(scope.key) is True

            # 释放子代理完成 -> 主动唤醒主代理并回传报告与生成媒体！
            release_sub.set()
            await _wait_until(lambda: len(sent_texts) == 3 and scope.key not in runtime.workers)
            assert not manager.has_active(scope.key)
            assert "源码调研结论：VLM 识别 + 传统擦除" in main_requests[5]
            assert "gen-sub-1" in runtime.sessions[scope.key].generated_media
        finally:
            await manager.close()
            await runtime.close()


async def test_subagent_completion_merges_mid_turn_when_main_is_busy(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.groups["a"].policy.categories["subagent"] = True
    registry = ToolRegistry(lambda: store.settings)

    main_tool_entered = asyncio.Event()
    release_main_tool = asyncio.Event()
    sub_entered = asyncio.Event()
    release_sub = asyncio.Event()

    async def main_slow(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        main_tool_entered.set()
        await release_main_tool.wait()
        return ToolResult.success({"done": True})

    async def sub_slow(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        sub_entered.set()
        await release_sub.wait()
        return ToolResult.success({"ok": True})

    registry.add(
        ToolDefinition(
            "qq__main_slow", "主慢工具", "messages", json_binding(schema(Empty), main_slow)
        )
    )
    registry.add(
        ToolDefinition(
            "work__sub_slow", "子慢工具", "messages", json_binding(schema(Empty), sub_slow)
        )
    )

    main_payloads: list[str] = []
    main_step = 0
    sub_step = 0

    def transport_fn(request: httpx.Request) -> httpx.Response:
        nonlocal main_step, sub_step
        body = json.loads(request.content)
        if "后台执行子智能体" in str(body["messages"][0]["content"]):
            sub_step += 1
            if sub_step == 1:
                return httpx.Response(200, json=_chat_tool_call("sc1", "work__sub_slow", {}))
            return httpx.Response(200, json=answer("chat_completions", "MID_TURN_SUBAGENT_REPORT"))
        main_step += 1
        main_payloads.append(json.dumps(body, ensure_ascii=False))
        if main_step == 1:
            return httpx.Response(
                200,
                json=_chat_tool_call(
                    "m1", "subagent__spawn", {"title": "后台调研", "task": "做调研"}
                ),
            )
        if main_step == 2:
            # 派发后继续处理主工具（模拟主代理正在干别的事）
            return httpx.Response(200, json=_chat_tool_call("m2", "qq__main_slow", {}))
        return httpx.Response(200, json=answer("chat_completions", SILENT_RESPONSE))

    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_fn)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        manager = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda *args: ModelSession(*args, client=client),
            on_wake=runtime.wake_subagent,
            on_record=runtime.record,
            get_parent_session_id=runtime.session_id_for,
        )
        runtime.subagents = manager
        for tool in subagent_tools(manager):
            registry.add(tool)
        try:
            await runtime.receive(
                Message(
                    scope=scope,
                    message_id="m-busy",
                    sender_id="u1",
                    segments=[
                        {"type": "at", "data": {"qq": "bot"}},
                        {"type": "text", "data": {"text": "开干"}},
                    ],
                )
            )
            await asyncio.wait_for(
                asyncio.gather(main_tool_entered.wait(), sub_entered.wait()), 2.0
            )
            # 在主代理忙着跑 qq__main_slow 时，子代理完成！
            release_sub.set()
            await _wait_until(lambda: not manager.has_active(scope.key))
            # 释放主工具，主代理同轮下一步应立即看到实时并入的子代理回执！
            release_main_tool.set()
            await _wait_until(lambda: scope.key not in runtime.workers)
            assert len(main_payloads) == 3
            assert "MID_TURN_SUBAGENT_REPORT" in main_payloads[2]
        finally:
            await manager.close()
            await runtime.close()


async def test_subagent_steer_resume_inspect_and_scope_isolation(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.groups["a"].policy.categories["subagent"] = True
    store.settings.groups["b"] = store.settings.groups["a"].model_copy(deep=True)

    registry = ToolRegistry(lambda: store.settings)
    step1_entered = asyncio.Event()
    release_step1 = asyncio.Event()

    async def probe(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        step1_entered.set()
        await release_step1.wait()
        return ToolResult.success({"raw": "base64://SECRET_INLINE_DATA_1234567890", "ok": True})

    registry.add(
        ToolDefinition("work__probe", "探测", "messages", json_binding(schema(Empty), probe))
    )

    seen_sub_bodies: list[str] = []
    sub_call_count = 0

    def sub_transport(request: httpx.Request) -> httpx.Response:
        nonlocal sub_call_count
        sub_call_count += 1
        seen_sub_bodies.append(request.content.decode("utf-8"))
        if sub_call_count == 1:
            return httpx.Response(
                200, json=_chat_tool_call("c1", "work__probe", {}, reasoning="HIDDEN_REASONING")
            )
        if sub_call_count == 2:
            return httpx.Response(200, json=answer("chat_completions", "第一轮结论_" + ("A" * 40)))
        return httpx.Response(200, json=answer("chat_completions", "续跑第二轮结论：已按追问补充"))

    wakes: list[tuple[str, str]] = []

    async def on_wake(
        _scope: ConversationScope,
        _sid: str,
        _eid: str,
        etype: str,
        _title: str,
        text: str,
    ) -> bool:
        wakes.append((etype, text))
        return True

    scope_a = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    scope_b = ConversationScope(bot_id="bot", kind="group", peer_id="b")
    ctx_a = ToolContext(scope=scope_a, turn_id="t-a", napcat=Bot())
    ctx_b = ToolContext(scope=scope_b, turn_id="t-b", napcat=Bot())

    async with httpx.AsyncClient(transport=httpx.MockTransport(sub_transport)) as client:
        manager = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda *args: ModelSession(*args, client=client),
            on_wake=on_wake,
        )
        for tool in subagent_tools(manager):
            registry.add(tool)
        try:
            spawned = await registry.execute(
                ctx_a,
                "subagent__spawn",
                {"title": "源码检查", "task": "检查项目结构"},
            )
            assert spawned.status == "ok" and isinstance(spawned.data, dict)
            sub_id = str(spawned.data["subagent_id"])
            await asyncio.wait_for(step1_entered.wait(), 2.0)

            # 跨群隔离：群 B 看不到也碰不到群 A 的子代理
            list_b = await registry.execute(ctx_b, "subagent__list", {})
            assert (
                list_b.status == "ok"
                and isinstance(list_b.data, dict)
                and list_b.data["total"] == 0
            )
            assert (
                await registry.execute(ctx_b, "subagent__inspect", {"subagent_id": sub_id})
            ).error_type == "SubagentNotFound"
            assert (
                await registry.execute(
                    ctx_b, "subagent__send", {"subagent_id": sub_id, "message": "干扰"}
                )
            ).error_type == "SubagentNotFound"
            assert (
                await registry.execute(ctx_b, "subagent__cancel", {"subagent_id": sub_id})
            ).error_type == "SubagentNotFound"

            # 运行中发送纠偏指令
            steered = await registry.execute(
                ctx_a,
                "subagent__send",
                {"subagent_id": sub_id, "message": "别看测试目录，重点看核心模块"},
            )
            assert steered.status == "ok" and isinstance(steered.data, dict)
            assert steered.data["mode"] == "steered"
            release_step1.set()
            await _wait_until(lambda: not manager.has_active(scope_a.key))

            # 第 2 次模型请求中收到了实时注入的纠偏指令
            assert "别看测试目录，重点看核心模块" in seen_sub_bodies[1]
            assert wakes[-1][0] == "completed"

            # inspect 脱敏且隐藏推理与 base64，并支持 report_offset
            inspected = await registry.execute(
                ctx_a, "subagent__inspect", {"subagent_id": sub_id, "report_offset": 6}
            )
            assert inspected.status == "ok" and isinstance(inspected.data, dict)
            dumped = json.dumps(inspected.data, ensure_ascii=False)
            assert "HIDDEN_REASONING" not in dumped
            assert "SECRET_INLINE_DATA" not in dumped
            assert "base64://[内嵌数据已省略]" in dumped
            assert str(inspected.data["report_chunk"]).startswith("A" * 10)

            # 已结束后通过 subagent__send 原上下文续跑
            resumed = await registry.execute(
                ctx_a,
                "subagent__send",
                {"subagent_id": sub_id, "message": "再补充确认下边界条件", "extra_steps": 10},
            )
            assert resumed.status == "ok" and isinstance(resumed.data, dict)
            assert resumed.data["mode"] == "resumed"
            await _wait_until(lambda: not manager.has_active(scope_a.key))
            assert "再补充确认下边界条件" in seen_sub_bodies[2]
            assert "第一轮结论_" in seen_sub_bodies[2]
            assert wakes[-1][0] == "completed"
            assert "续跑第二轮结论" in wakes[-1][1]
        finally:
            await manager.close()


async def test_subagent_step_limit_wrapup_readonly_loop_guard_and_watchdog(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.groups["a"].policy.categories["subagent"] = True
    registry = ToolRegistry(lambda: store.settings)

    async def step_read(_ctx: ToolContext, args: _StepArg) -> ToolResult:
        return ToolResult.success({"val": args.n})

    stall_entered = asyncio.Event()
    never_finish = asyncio.Event()

    async def hang_tool(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        stall_entered.set()
        await never_finish.wait()
        return ToolResult.success()

    registry.add(
        ToolDefinition(
            "work__read",
            "只读查询",
            "messages",
            model_binding(_StepArg, step_read),
            concurrency_safe=True,
        )
    )
    registry.add(
        ToolDefinition("work__hang", "阻塞工具", "messages", json_binding(schema(Empty), hang_tool))
    )

    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    ctx = ToolContext(scope=scope, turn_id="t1", napcat=Bot())

    # 1. 步数耗尽时强制无工具收尾（不丢弃已有调研）
    wrap_bodies: list[JsonObject] = []
    wrap_step = 0

    def wrap_transport(request: httpx.Request) -> httpx.Response:
        nonlocal wrap_step
        wrap_step += 1
        body = json.loads(request.content)
        wrap_bodies.append(body)
        if wrap_step <= 5:
            return httpx.Response(
                200, json=_chat_tool_call(f"w{wrap_step}", "work__read", {"n": wrap_step})
            )
        # 第 6 次强制收尾调用不允许再带 tools
        assert body.get("tools") == []
        return httpx.Response(200, json=answer("chat_completions", "阶段性汇总：已查完前 5 项"))

    wakes: list[tuple[str, str]] = []

    async def on_wake(
        _s: ConversationScope, _sid: str, _eid: str, etype: str, _t: str, text: str
    ) -> bool:
        wakes.append((etype, text))
        return True

    async with httpx.AsyncClient(transport=httpx.MockTransport(wrap_transport)) as client:
        mgr = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda *args: ModelSession(*args, client=client),
            on_wake=on_wake,
        )
        for tool in subagent_tools(mgr):
            registry.add(tool)
        try:
            res = await registry.execute(
                ctx, "subagent__spawn", {"task": "多步探索", "max_steps": 5}
            )
            assert res.status == "ok" and isinstance(res.data, dict)
            sub_id = str(res.data["subagent_id"])
            await _wait_until(lambda: not mgr.has_active(scope.key))
            rec = mgr.records[sub_id]
            assert rec.state == "step_limit"
            assert rec.report == "阶段性汇总：已查完前 5 项"
            assert wakes[-1][0] == "step_limit"
            assert "阶段性汇总：已查完前 5 项" in wakes[-1][1]
        finally:
            await mgr.close()

    # 2. 只读 concurrency_safe 工具连续重复无变化同样会被循环拦截
    registry2 = ToolRegistry(lambda: store.settings)
    registry2.add(
        ToolDefinition(
            "work__read",
            "只读查询",
            "messages",
            model_binding(_StepArg, step_read),
            concurrency_safe=True,
        )
    )
    loop_step = 0
    loop_payloads: list[str] = []

    def loop_transport(request: httpx.Request) -> httpx.Response:
        nonlocal loop_step
        loop_step += 1
        loop_payloads.append(request.content.decode("utf-8"))
        if loop_step <= 6:
            return httpx.Response(
                200, json=_chat_tool_call(f"lp{loop_step}", "work__read", {"n": 1})
            )
        return httpx.Response(200, json=answer("chat_completions", "已停止重复读取"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(loop_transport)) as client:
        mgr2 = SubagentManager(
            lambda: store,
            registry2,
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        for tool in subagent_tools(mgr2):
            registry2.add(tool)
        try:
            res = await registry2.execute(
                ctx, "subagent__spawn", {"task": "重复读测试", "max_steps": 10}
            )
            assert res.status == "ok" and isinstance(res.data, dict)
            await _wait_until(lambda: not mgr2.has_active(scope.key))
            assert "RepeatedToolCallBlocked" in loop_payloads[-1]
        finally:
            await mgr2.close()

    # 3. 看门狗停滞告警 + 总时长硬超时
    watch_wakes: list[tuple[str, str]] = []

    async def on_watch_wake(
        _s: ConversationScope, _sid: str, _eid: str, etype: str, _t: str, text: str
    ) -> bool:
        watch_wakes.append((etype, text))
        return True

    def hang_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_tool_call("h1", "work__hang", {}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(hang_transport)) as client:
        mgr3 = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda *args: ModelSession(*args, client=client),
            on_wake=on_watch_wake,
            stall_notice_seconds=0.05,
            watchdog_poll_seconds=0.02,
        )
        try:
            req = SpawnSubagentInput.model_construct(
                task="会卡死的任务",
                title="卡死测试",
                context_hint="",
                max_steps=10,
                timeout_seconds=0.16,
                check_in_seconds=None,
            )
            rec = mgr3.spawn(ctx, req)
            await asyncio.wait_for(stall_entered.wait(), 1.0)
            await _wait_until(lambda: not mgr3.has_active(scope.key), timeout=2.0)
            assert rec.state == "timeout"
            event_types = [ev[0] for ev in watch_wakes]
            assert "stall_warning" in event_types
            assert event_types[-1] == "timeout"
        finally:
            await mgr3.close()


async def test_parallel_subagent_workspace_cancel_isolation_and_reset_cleanup(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "ws"
    work_dir.mkdir()
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.workspace = WorkspaceSettings(
        enabled=True, root=str(work_dir), command_timeout=10, concurrent_executions=2
    )
    store.settings.groups["a"].policy.categories["workspace"] = True
    store.settings.groups["a"].policy.categories["subagent"] = True

    both_started = asyncio.Event()
    finish_b = asyncio.Event()
    terminated: list[str] = []

    class DualProc:
        def __init__(self, cmd: str) -> None:
            self.cmd = cmd
            self.stopped = asyncio.Event()

        def output(self) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                if self.cmd == "cmd_a":
                    yield b"running-a"
                    await self.stopped.wait()
                else:
                    await finish_b.wait()
                    yield b"done-b"

            return _gen()

        async def wait(self) -> int:
            if self.cmd == "cmd_a":
                await self.stopped.wait()
                return -9
            await finish_b.wait()
            return 0

        async def terminate(self) -> None:
            terminated.append(self.cmd)
            self.stopped.set()

    class DualSpawner:
        def __init__(self) -> None:
            self.count = 0

        async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
            self.count += 1
            if self.count >= 2:
                both_started.set()
            return DualProc(command)

    ws_service = WorkspaceService(lambda: store.settings.workspace, spawner=DualSpawner())
    registry = ToolRegistry(lambda: store.settings)
    for t in workspace_tools(ws_service):
        registry.add(t)

    def transport_fn(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "后台执行子智能体" not in str(body["messages"][0]["content"]):
            return httpx.Response(200, json=answer("chat_completions", SILENT_RESPONSE))
        text = json.dumps(body, ensure_ascii=False)
        if len(body["messages"]) <= 3:
            cmd = "cmd_a" if "TASK_A" in text else "cmd_b"
            return httpx.Response(
                200, json=_chat_tool_call(f"exec-{cmd}", "workspace__exec", {"command": cmd})
            )
        return httpx.Response(200, json=answer("chat_completions", "命令执行完毕"))

    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_fn)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        manager = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda *args: ModelSession(*args, client=client),
            on_wake=runtime.wake_subagent,
            on_record=runtime.record,
            get_parent_session_id=runtime.session_id_for,
        )
        runtime.subagents = manager
        for t in subagent_tools(manager):
            registry.add(t)

        # 建立主会话对象以测试 reset 行为
        await runtime.receive(
            Message(
                scope=scope,
                message_id="init-1",
                sender_id="u1",
                segments=[
                    {"type": "at", "data": {"qq": "bot"}},
                    {"type": "text", "data": {"text": "hi"}},
                ],
            )
        )
        await _wait_until(lambda: scope.key not in runtime.workers)
        ctx = ToolContext(scope=scope, turn_id="t-parent", napcat=Bot())
        try:
            r_a = await registry.execute(
                ctx, "subagent__spawn", {"title": "任务A", "task": "TASK_A"}
            )
            r_b = await registry.execute(
                ctx, "subagent__spawn", {"title": "任务B", "task": "TASK_B"}
            )
            assert r_a.status == "ok" and isinstance(r_a.data, dict)
            assert r_b.status == "ok" and isinstance(r_b.data, dict)
            id_a, id_b = str(r_a.data["subagent_id"]), str(r_b.data["subagent_id"])
            await asyncio.wait_for(both_started.wait(), 2.0)

            # 取消子代理 A 只终止 cmd_a，绝不误杀子代理 B 的 cmd_b！
            cancel_a = await registry.execute(
                ctx, "subagent__cancel", {"subagent_id": id_a, "reason": "跑偏"}
            )
            assert cancel_a.status == "ok"
            assert manager.records[id_a].state == "cancelled"
            assert set(terminated) == {"cmd_a"} and "cmd_b" not in terminated
            assert manager.records[id_b].active is True

            # 有活跃子代理时 reset(stop=False) 应拒绝；reset(stop=True) 应清理全部子代理
            with pytest.raises(SessionBusy):
                await runtime.reset(scope.key, stop=False)
            await runtime.reset(scope.key, stop=True)
            assert manager.list_for_scope(scope.key) == []
            assert "cmd_b" in terminated
        finally:
            finish_b.set()
            await manager.close()
            await runtime.close()
            await ws_service.close()


async def test_subagent_skills_catalog_and_deferred_readonly_tool_search(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.groups["a"].policy.categories["skills"] = True
    store.settings.groups["a"].policy.categories["subagent"] = True
    store.save_file(
        "skills/repo-audit.md",
        "---\nname: repo-audit\ndescription: 仓库源码审计规范\n---\n先查分支再读核心入口。\n",
        expected_sha=None,
    )

    registry = ToolRegistry(lambda: store.settings)
    for t in skill_tools(lambda: store):
        registry.add(t)

    async def dummy(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        return ToolResult.success({"ok": True})

    registry.add(
        ToolDefinition(
            "qq__get_extra_info",
            "扩展只读查询",
            "messages",
            json_binding(schema(Empty), dummy),
            deferred=True,
            concurrency_safe=True,
        )
    )
    registry.add(
        ToolDefinition(
            "qq__ban_extra_user",
            "扩展群管写入",
            "messages",
            json_binding(schema(Empty), dummy),
            deferred=True,
            concurrency_safe=False,
        )
    )

    seen_bodies: list[JsonObject] = []
    step = 0

    def transport_fn(request: httpx.Request) -> httpx.Response:
        nonlocal step
        step += 1
        body = json.loads(request.content)
        seen_bodies.append(body)
        if step == 1:
            return httpx.Response(200, json=_chat_tool_call("s1", "qq__search_tools", {}))
        return httpx.Response(200, json=answer("chat_completions", "检索完成"))

    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    ctx = ToolContext(scope=scope, turn_id="t1", napcat=Bot())
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_fn)) as client:
        manager = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        for t in subagent_tools(manager):
            registry.add(t)
        try:
            res = await registry.execute(ctx, "subagent__spawn", {"task": "查找可用工具和技能"})
            assert res.status == "ok"
            await _wait_until(lambda: not manager.has_active(scope.key))
            first_json = json.dumps(seen_bodies[0], ensure_ascii=False)
            assert "可用技能目录" in first_json and "repo-audit" in first_json
            tools_raw = seen_bodies[0].get("tools")
            assert isinstance(tools_raw, list)
            first_tools = [
                fn["name"]
                for item in tools_raw
                if isinstance(item, dict) and isinstance(fn := item.get("function"), dict)
            ]
            assert "qq__search_tools" in first_tools
            # 第二步结果中只解锁只读扩展工具，绝不暴露群管写入工具
            second_json = json.dumps(seen_bodies[1], ensure_ascii=False)
            assert "qq__get_extra_info" in second_json
            assert "qq__ban_extra_user" not in second_json
        finally:
            await manager.close()


def test_subagent_web_api_detail_version_steer_resume_and_cancel(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.groups["a"].policy.categories["subagent"] = True
    store.path.write_text(
        tomlkit.dumps(store.settings.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    store.reload()

    app = create_app(store.path, connect_mcp=False)
    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")

    step_no = 0
    seen_texts: list[str] = []

    def transport_fn(request: httpx.Request) -> httpx.Response:
        nonlocal step_no
        step_no += 1
        seen_texts.append(request.content.decode("utf-8"))
        if step_no == 1:
            return httpx.Response(200, json=answer("chat_completions", "控制台可见的初轮结论"))
        return httpx.Response(200, json=answer("chat_completions", "管理员续跑后的补充结论"))

    with TestClient(app) as client:
        mock_http = httpx.AsyncClient(transport=httpx.MockTransport(transport_fn))
        app.state.runtime.session_factory = lambda *args: ModelSession(*args, client=mock_http)

        # 启动一个子代理并等待其完成
        sub_ctx = ToolContext(scope=scope, turn_id="t-web", napcat=Bot())

        async def _spawn_and_wait() -> str:
            rec = app.state.subagents.spawn(
                sub_ctx,
                SpawnSubagentInput(title="控制台观测测试", task="测试 WebUI 子代理接口"),
            )
            if rec.task_handle is not None:
                await rec.task_handle
            return rec.id

        sub_id = client.portal.call(_spawn_and_wait) if client.portal else ""
        assert sub_id.startswith("sub_")

        # 1. /api/runtime 包含 subagents 列表
        rt = client.get("/api/runtime").json()
        assert any(item["subagent_id"] == sub_id for item in rt["subagents"])

        # 2. /api/subagents/detail 返回投影条目（含 task 与 assistant）且支持 since 增量缓存
        detail = client.get("/api/subagents/detail", params={"id": sub_id}).json()
        assert detail["subagent_id"] == sub_id
        assert detail["state"] == "completed"
        assert detail["report"] == "控制台可见的初轮结论"
        kinds = [entry["kind"] for entry in detail["entries"]]
        assert (
            "system" in kinds and "task" in kinds and "assistant" in kinds and "boundary" in kinds
        )

        cached = client.get(
            "/api/subagents/detail", params={"id": sub_id, "since": detail["version"]}
        ).json()
        assert cached["entries"] is None

        # 3. 管理员通过 /api/subagents/send 续跑子代理
        resumed = client.post(
            "/api/subagents/send",
            json={"subagent_id": sub_id, "message": "请再核对一下细节", "extra_steps": 8},
        )
        assert resumed.status_code == 200
        assert resumed.json()["mode"] == "resumed"

        async def _wait_resumed() -> None:
            rec = app.state.subagents.records[sub_id]
            if rec.task_handle is not None:
                await rec.task_handle

        if client.portal:
            client.portal.call(_wait_resumed)

        after = client.get("/api/subagents/detail", params={"id": sub_id}).json()
        assert after["state"] == "completed"
        assert after["report"] == "管理员续跑后的补充结论"
        assert any(
            entry["kind"] == "steer" and entry.get("resume") is True for entry in after["entries"]
        )

        # 4. 不存在的子代理返回 404
        assert client.get("/api/subagents/detail", params={"id": "sub_missing"}).status_code == 404
        assert (
            client.post("/api/subagents/cancel", json={"subagent_id": "sub_missing"}).status_code
            == 404
        )
        assert (
            client.post(
                "/api/subagents/send", json={"subagent_id": "sub_missing", "message": "hi"}
            ).status_code
            == 404
        )
        if client.portal:
            client.portal.call(mock_http.aclose)
