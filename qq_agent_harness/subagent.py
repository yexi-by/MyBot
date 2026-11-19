"""有界内存异步子智能体（Subagent）管理与监管工具集。

子代理在独立后台协程与独立上下文中运行，不阻塞主会话响应新消息或 @；
支持并行派生、状态巡检、会话轨迹只读抽查、运行中实时纠偏、结束后原上下文续跑、
防卡死看门狗与步数上限自动收尾，并通过事件唤醒机制主动通知主会话。
"""

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from .configuration import ConfigStore
from .contracts import (
    ConversationScope,
    GeneratedMedia,
    JsonObject,
    JsonValue,
    Model,
    NapCatConnection,
    OrderedSet,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
    remember_generated,
)
from .providers import (
    CompactionError,
    ContextLimitError,
    ModelSession,
    ProviderError,
    ToolCall,
)
from .registry import ToolRegistry
from .session_view import project, scrub
from .skills import SKILL_TOOL_NAME, discover_skills

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {"subagent": "子任务委派"}

DEFAULT_MAX_STEPS = 30
MIN_MAX_STEPS = 5
MAX_MAX_STEPS = 60
DEFAULT_RESUME_STEPS = 20

DEFAULT_TIMEOUT_SECONDS = 900.0
MIN_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 3600.0

MIN_CHECK_IN_SECONDS = 30.0
MAX_CHECK_IN_SECONDS = 1800.0
DEFAULT_STALL_NOTICE_SECONDS = 180.0
MAX_STALL_NOTICES_PER_RUN = 3

MAX_ACTIVE_PER_SCOPE = 3
MAX_ACTIVE_TOTAL = 8
MAX_RETAINED_SUBAGENTS = 40
MAX_RETAINED_BYTES = 64 * 1024 * 1024

MAX_PARALLEL_READ_TOOLS = 8
REPEAT_REMINDER_THRESHOLDS = (3, 5)
REPEAT_BLOCK_THRESHOLD = 6

MAX_INLINE_REPORT_CHARS = 12_000
MAX_INSPECT_ENTRY_CHARS = 3_000
MAX_INSPECT_TOTAL_CHARS = 16_000

SubagentState = Literal[
    "running",
    "retrying",
    "compacting",
    "completed",
    "step_limit",
    "timeout",
    "failed",
    "cancelled",
]
ACTIVE_SUBAGENT_STATES: frozenset[str] = frozenset({"running", "retrying", "compacting"})
SteerSource = Literal["main_agent", "operator"]

SUBAGENT_SYSTEM_PREFIX = """你是主会话派生的后台执行子智能体，负责在独立上下文中完成指定的繁重分析、检索、源码阅读、媒体处理或工作区计算任务。
- 你没有直接向 QQ 发送消息或执行群管写入的工具；请专注于调查、运行验证并产出清晰准确的报告交回主会话。
- 工作区中单次命令临时文件写 $TMPDIR（执行完自动销毁），跨步骤临时素材放 .scratch/，需要交回主会话复用的文件或生成媒体请明确记录其 workspace_path 或 generated_id。
- 若上下文中列出了可用技能目录，遇到对应领域任务请先调用 `skill__load` 读取规范；需要更多只读 QQ 查询能力时可调用 `qq__search_tools`。
- 每次请求都会在「本轮状态」中附带当前步数、剩余步数与已耗时，请据此把控节奏；若收到主会话或管理员的插话纠偏指令，请立即优先按最新要求调整方向。
- 任务完成后，直接输出结构清晰、事实准确的总结报告供主会话使用；未确认的结果严禁臆测。"""


class SubagentNotFound(LookupError):
    """子代理不存在、不属于当前会话或已被内存容量回收。"""


class SubagentBusyError(RuntimeError):
    """子代理并发数达到上限或当前状态不允许该操作。"""


class SpawnSubagentInput(Model):
    task: str = Field(
        min_length=1,
        max_length=8000,
        description="委派给子智能体的明确任务目标、输入材料路径/ID 与期望产出的报告要点",
    )
    title: str = Field(
        default="",
        max_length=80,
        description="可选：简短任务标题（便于主会话和控制台区分多个子代理）；留空自动从 task 截取",
    )
    context_hint: str = Field(
        default="",
        max_length=4000,
        description="可选：补充给子智能体的简短会话背景说明",
    )
    max_steps: int = Field(
        default=DEFAULT_MAX_STEPS,
        ge=MIN_MAX_STEPS,
        le=MAX_MAX_STEPS,
        description="可选：本次运行允许的最大模型步数（默认 30，范围 5–60）；达上限会自动汇总阶段成果",
    )
    timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        ge=MIN_TIMEOUT_SECONDS,
        le=MAX_TIMEOUT_SECONDS,
        description="可选：本次运行总超时上限秒数（默认 900 秒，范围 30–3600 秒）",
    )
    check_in_seconds: float | None = Field(
        default=None,
        ge=MIN_CHECK_IN_SECONDS,
        le=MAX_CHECK_IN_SECONDS,
        description="可选：定时巡检间隔秒数（30–1800 秒）；到点未完成会主动通知主会话检查是否跑偏或卡死",
    )


class ListSubagentsInput(Model):
    include_finished: bool = Field(
        default=True,
        description="是否包含近期已结束的子代理（默认 true）",
    )


class InspectSubagentInput(Model):
    subagent_id: str = Field(min_length=1, max_length=64, description="要查看的子代理 ID")
    tail: int = Field(
        default=12,
        ge=1,
        le=60,
        description="读取末尾最近多少条可见会话记录（默认 12）；传入 offset 时按偏移分页",
    )
    offset: int | None = Field(
        default=None,
        ge=0,
        description="可选：0 基起始条目偏移，用于从头或按页阅读子代理轨迹",
    )
    include_report: bool = Field(
        default=True,
        description="是否附带当前最新报告文本（支持 report_offset 分段读取超长报告）",
    )
    report_offset: int = Field(
        default=0,
        ge=0,
        description="可选：读取完整报告时的字符起始偏移（默认 0）",
    )


class SendSubagentInput(Model):
    subagent_id: str = Field(min_length=1, max_length=64, description="目标子代理 ID")
    message: str = Field(
        min_length=1,
        max_length=6000,
        description="发给子代理的纠偏指令、补充线索（运行中实时注入）或追问任务（已结束时原上下文续跑）",
    )
    extra_steps: int | None = Field(
        default=None,
        ge=MIN_MAX_STEPS,
        le=MAX_MAX_STEPS,
        description="仅续跑已结束子代理时生效：本次续跑允许的最大步数（默认 20）",
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=MIN_TIMEOUT_SECONDS,
        le=MAX_TIMEOUT_SECONDS,
        description="仅续跑已结束子代理时生效：本次续跑总超时秒数",
    )


class CancelSubagentInput(Model):
    subagent_id: str = Field(min_length=1, max_length=64, description="要停止的子代理 ID")
    reason: str = Field(
        default="",
        max_length=300,
        description="可选：停止原因说明（如已跑偏、卡死或群友取消需求）",
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


def _derive_title(title: str, task: str) -> str:
    cleaned = " ".join(title.strip().split())
    if cleaned:
        return cleaned[:80]
    first_line = next((line.strip() for line in task.splitlines() if line.strip()), "")
    compact = " ".join(first_line.split())
    return compact[:48] + ("…" if len(compact) > 48 else "") if compact else "后台子任务"


def _source_label(source: SteerSource) -> str:
    return "控制台管理员" if source == "operator" else "主会话"


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[已截断，原长 {len(text)} 字]"


def _compact_json(value: JsonValue, limit: int) -> JsonValue:
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) <= limit:
        return value
    return _truncate_text(serialized, limit)


def _compact_entry(entry: JsonObject, per_entry_limit: int) -> JsonObject:
    kind = entry.get("kind")
    result: JsonObject = {"kind": kind, "turn": entry.get("turn"), "at": entry.get("at")}
    if kind == "task":
        result["title"] = entry.get("title")
        result["task"] = _truncate_text(str(entry.get("task") or ""), per_entry_limit)
        if hint := str(entry.get("context_hint") or ""):
            result["context_hint"] = _truncate_text(hint, per_entry_limit // 2)
        return result
    if kind == "steer":
        result["resume"] = bool(entry.get("resume"))
        raw_msgs = entry.get("messages")
        msgs: list[JsonValue] = []
        if isinstance(raw_msgs, list):
            for item in raw_msgs:
                if isinstance(item, dict):
                    msgs.append(
                        {
                            "source": str(item.get("source") or ""),
                            "text": _truncate_text(str(item.get("text") or ""), per_entry_limit),
                        }
                    )
        result["messages"] = msgs
        return result
    if kind == "call":
        result["name"] = entry.get("name")
        result["arguments"] = _compact_json(entry.get("arguments"), per_entry_limit // 2)
        raw_res = entry.get("result")
        if isinstance(raw_res, dict):
            compact_res: JsonObject = {"status": raw_res.get("status")}
            if raw_res.get("message"):
                compact_res["message"] = _truncate_text(str(raw_res["message"]), 400)
            if raw_res.get("error_type"):
                compact_res["error_type"] = raw_res.get("error_type")
            if raw_res.get("data") is not None:
                compact_res["data"] = _compact_json(raw_res["data"], per_entry_limit // 2)
            result["result"] = compact_res
        else:
            result["result"] = None
        return result
    if kind in ("assistant", "summary", "other", "transfer"):
        if entry.get("label"):
            result["label"] = entry.get("label")
        result["text"] = _truncate_text(str(entry.get("text") or ""), per_entry_limit)
        return result
    if kind == "notice":
        raw_notices = entry.get("messages")
        result["messages"] = (
            [_truncate_text(str(m), 500) for m in raw_notices]
            if isinstance(raw_notices, list)
            else []
        )
        return result
    if kind == "boundary":
        result["outcome"] = entry.get("outcome")
        return result
    if kind == "media":
        result["items"] = entry.get("items")
        return result
    return {k: _compact_json(v, per_entry_limit // 2) for k, v in entry.items()}


class _LiveNapCat:
    """按 bot_id 实时解析当前活跃 NapCat 连接，避免后台长任务持有重连前的旧连接。"""

    def __init__(
        self,
        get_napcat: Callable[[str], NapCatConnection] | None,
        bot_id: str,
        fallback: NapCatConnection,
    ) -> None:
        self._get_napcat = get_napcat
        self._bot_id = bot_id
        self._fallback = fallback

    async def call(
        self, action: str, params: JsonObject, *, timeout: float | None = None
    ) -> JsonValue:
        conn = self._get_napcat(self._bot_id) if self._get_napcat is not None else self._fallback
        return await conn.call(action, params, timeout=timeout)


@dataclass
class SubagentRecord:
    id: str
    scope: ConversationScope
    parent_session_id: str
    title: str
    task: str
    context_hint: str
    model: ModelSession | None
    state: SubagentState
    created_at: float
    started_at: float
    updated_at: float
    last_activity_at: float
    max_steps: int
    timeout_seconds: float
    check_in_seconds: float | None
    discovered_tools: OrderedSet = field(default_factory=OrderedSet)
    read_workspace: Callable[[str], Awaitable[bytes]] | None = None
    write_workspace: Callable[..., Awaitable[None]] | None = None
    fallback_napcat: NapCatConnection | None = None
    fallback_generated_media: OrderedDict[str, GeneratedMedia] | None = None
    finished_at: float | None = None
    steps: int = 0
    total_steps: int = 0
    current_tool: str = ""
    current_tool_started_at: float | None = None
    last_tool: str = ""
    report: str = ""
    error: str = ""
    generated_ids: list[str] = field(default_factory=list)
    retained_bytes: int = 0
    cancel_requested: bool = False
    stop_reason: str = ""
    task_handle: asyncio.Task[None] | None = None
    watchdog_handle: asyncio.Task[None] | None = None
    inbox: deque[tuple[SteerSource, str]] = field(default_factory=deque)
    stall_notices_sent: int = 0
    last_stall_notice_at: float = 0.0
    run_generation: int = 1
    event_seq: int = 0

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_SUBAGENT_STATES

    def measure(self) -> int:
        if self.model is None:
            return 0
        return len(json.dumps(self.model.items, ensure_ascii=False).encode("utf-8"))

    def elapsed(self, now: float | None = None) -> float:
        ref = time.time() if now is None else now
        end = self.finished_at if (not self.active and self.finished_at is not None) else ref
        return round(max(0.0, end - self.started_at), 1)

    def idle_seconds(self, now: float | None = None) -> float:
        if not self.active:
            return 0.0
        ref = time.time() if now is None else now
        return round(max(0.0, ref - self.last_activity_at), 1)

    def summary(self, now: float | None = None) -> JsonObject:
        ref = time.time() if now is None else now
        model = self.model
        return {
            "subagent_id": self.id,
            "scope_key": self.scope.key,
            "title": self.title,
            "state": self.state,
            "turns": model.turn if model else 0,
            "step": self.steps,
            "total_steps": self.total_steps,
            "max_steps": self.max_steps,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "last_activity_at": self.last_activity_at,
            "elapsed_seconds": self.elapsed(ref),
            "idle_seconds": self.idle_seconds(ref),
            "timeout_seconds": self.timeout_seconds,
            "check_in_seconds": self.check_in_seconds,
            "current_tool": self.current_tool,
            "current_tool_started_at": self.current_tool_started_at,
            "last_tool": self.last_tool,
            "model": model.settings.model if model else "",
            "context": model.context_state if model else {},
            "retained_bytes": self.retained_bytes,
            "generated_ids": [gid for gid in self.generated_ids],
            "pending_steers": len(self.inbox),
            "error": self.error,
            "can_resume": model is not None and not self.active,
        }

    def action_summary(self, now: float | None = None) -> JsonObject:
        ref = time.time() if now is None else now
        payload: JsonObject = {
            "subagent_id": self.id,
            "title": self.title,
            "state": self.state,
            "step": self.steps,
            "max_steps": self.max_steps,
            "elapsed_seconds": self.elapsed(ref),
            "idle_seconds": self.idle_seconds(ref),
        }
        if self.current_tool:
            payload["current_tool"] = self.current_tool
        elif self.last_tool:
            payload["last_tool"] = self.last_tool
        if self.inbox:
            payload["pending_steers"] = len(self.inbox)
        return payload

    def detail(self, *, since: int | None = None) -> JsonObject:
        now = time.time()
        model = self.model
        entries: JsonValue = None
        if model is None:
            entries = []
        elif since != model.version:
            entries = [entry for entry in project(model)]
        return {
            **self.summary(now),
            "task": self.task,
            "context_hint": self.context_hint,
            "report": self.report,
            "version": model.version if model else 0,
            "queued_steers": [
                {"source": _source_label(src), "text": text} for src, text in self.inbox
            ],
            "entries": entries,
        }


WakeCallback = Callable[[ConversationScope, str, str, str, str, str], Awaitable[bool]]
RecordCallback = Callable[..., None]
MediaCallback = Callable[[str, str, GeneratedMedia], bool]
SessionIdLookup = Callable[[str], str]


class SubagentManager:
    """有界内存后台子代理管理器。"""

    def __init__(
        self,
        get_store: Callable[[], ConfigStore],
        registry: ToolRegistry,
        *,
        session_factory: Callable[..., ModelSession] = ModelSession,
        get_napcat: Callable[[str], NapCatConnection] | None = None,
        on_wake: WakeCallback | None = None,
        on_record: RecordCallback | None = None,
        on_media: MediaCallback | None = None,
        get_parent_session_id: SessionIdLookup | None = None,
        stall_notice_seconds: float = DEFAULT_STALL_NOTICE_SECONDS,
        watchdog_poll_seconds: float = 1.0,
        max_active_per_scope: int = MAX_ACTIVE_PER_SCOPE,
        max_active_total: int = MAX_ACTIVE_TOTAL,
        max_retained: int = MAX_RETAINED_SUBAGENTS,
        max_retained_bytes: int = MAX_RETAINED_BYTES,
    ) -> None:
        self.get_store = get_store
        self.registry = registry
        self.session_factory = session_factory
        self.get_napcat = get_napcat
        self.on_wake = on_wake
        self.on_record = on_record
        self.on_media = on_media
        self.get_parent_session_id = get_parent_session_id
        self.stall_notice_seconds = stall_notice_seconds
        self.watchdog_poll_seconds = watchdog_poll_seconds
        self.max_active_per_scope = max_active_per_scope
        self.max_active_total = max_active_total
        self.max_retained = max_retained
        self.max_retained_bytes = max_retained_bytes
        self.records: OrderedDict[str, SubagentRecord] = OrderedDict()
        self.closed = False

    def _emit_record(self, scope_key: str, message: str, **extra: str) -> None:
        if self.on_record is not None:
            self.on_record(scope_key, "subagent", message, **extra)

    def _active_counts(self, scope_key: str) -> tuple[int, int]:
        total = 0
        scoped = 0
        for rec in self.records.values():
            if rec.active:
                total += 1
                if rec.scope.key == scope_key:
                    scoped += 1
        return scoped, total

    def _check_capacity(self, scope_key: str) -> None:
        if self.closed:
            raise SubagentBusyError("服务正在关闭，无法启动子代理")
        scoped, total = self._active_counts(scope_key)
        if scoped >= self.max_active_per_scope:
            raise SubagentBusyError(
                f"当前会话同时运行的子代理已达上限（{self.max_active_per_scope} 个），"
                "请等待现有子代理完成或先取消无关子代理"
            )
        if total >= self.max_active_total:
            raise SubagentBusyError(
                f"全局同时运行的子代理已达上限（{self.max_active_total} 个），请稍后再试"
            )

    def _inject_skills(self, session: ModelSession, scope: ConversationScope) -> None:
        store = self.get_store()
        skill_tool = self.registry.tools.get(SKILL_TOOL_NAME)
        if skill_tool is None or not self.registry.permitted(scope, skill_tool)[0]:
            return
        skills = [(item.name, item.description) for item in discover_skills(store)]
        if not skills:
            return
        session.add_background(
            json.dumps(
                {
                    "可用技能目录": [{"名称": name, "说明": desc} for name, desc in skills],
                    "说明": "遇到相关领域需求时，先调用 skill__load 读取完整规范再执行。",
                },
                ensure_ascii=False,
            ),
            "background",
        )

    def spawn(self, context: ToolContext, request: SpawnSubagentInput) -> SubagentRecord:
        scope = context.scope
        self._check_capacity(scope.key)
        store = self.get_store()
        endpoint = store.settings.session_endpoint(scope)
        if endpoint is None:
            raise ValueError("当前会话未配置有效模型")

        sub_id = "sub_" + uuid.uuid4().hex[:10]
        title = _derive_title(request.title, request.task)
        parent_session_id = (
            self.get_parent_session_id(scope.key) if self.get_parent_session_id is not None else ""
        )
        prompt_payload: JsonObject = {
            "来源会话": scope.model_dump(mode="json"),
            "任务标题": title,
            "委派任务": request.task,
        }
        if request.context_hint.strip():
            prompt_payload["补充背景"] = request.context_hint.strip()

        session = self.session_factory(
            endpoint,
            SUBAGENT_SYSTEM_PREFIX,
            json.dumps(prompt_payload, ensure_ascii=False),
        )
        session.media_limit = store.settings.napcat.media_max_bytes
        self._inject_skills(session, scope)

        now = time.time()
        record = SubagentRecord(
            id=sub_id,
            scope=scope,
            parent_session_id=parent_session_id,
            title=title,
            task=request.task,
            context_hint=request.context_hint.strip(),
            model=session,
            state="running",
            created_at=now,
            started_at=now,
            updated_at=now,
            last_activity_at=now,
            last_stall_notice_at=now,
            max_steps=request.max_steps,
            timeout_seconds=request.timeout_seconds,
            check_in_seconds=request.check_in_seconds,
            discovered_tools=OrderedSet(context.discovered_tools),
            read_workspace=context.read_workspace,
            write_workspace=context.write_workspace,
            fallback_napcat=context.napcat,
            fallback_generated_media=context.generated_media,
        )
        record.retained_bytes = record.measure()
        self.records[sub_id] = record
        self.records.move_to_end(sub_id)
        self._emit_record(scope.key, f"启动后台子代理「{title}」（{sub_id}）", status="running")
        record.task_handle = asyncio.create_task(self._run(record, record.run_generation))
        return record

    async def send_message(
        self,
        subagent_id: str,
        message: str,
        *,
        source: SteerSource,
        scope: ConversationScope | None = None,
        extra_steps: int | None = None,
        timeout_seconds: float | None = None,
    ) -> JsonObject:
        record = self._get(subagent_id, scope=scope)
        text = message.strip()
        if not text:
            raise ValueError("指示内容不能为空")
        now = time.time()
        if record.active:
            record.inbox.append((source, text))
            record.last_activity_at = now
            record.updated_at = now
            self.records.move_to_end(record.id)
            self._emit_record(
                record.scope.key,
                f"向子代理「{record.title}」注入纠偏指令（{_source_label(source)}）",
                status="running",
            )
            return {
                "subagent_id": record.id,
                "title": record.title,
                "mode": "steered",
                "state": record.state,
                "queued_messages": len(record.inbox),
                "notice": "纠偏指令已加入队列，将在子代理下一步边界实时注入。",
            }

        if record.model is None:
            raise SubagentBusyError("该子代理上下文已释放，无法续跑")
        if self.get_parent_session_id is not None and record.parent_session_id:
            current_parent = self.get_parent_session_id(record.scope.key)
            if current_parent != record.parent_session_id:
                raise SubagentBusyError("所属主会话已重置，不能续跑旧会话的子代理")
        self._check_capacity(record.scope.key)
        store = self.get_store()
        if not store.settings.conversation(record.scope).enabled:
            raise ValueError("所属会话当前未启用，无法续跑子代理")
        endpoint = store.settings.session_endpoint(record.scope)
        if endpoint is None:
            raise ValueError("所属会话未配置有效模型，无法续跑子代理")

        await record.model.configure(endpoint, SUBAGENT_SYSTEM_PREFIX)
        record.model.media_limit = store.settings.napcat.media_max_bytes
        resume_payload: JsonObject = {
            "来源会话": record.scope.model_dump(mode="json"),
            "来源": _source_label(source),
            "子代理续跑指令": text,
        }
        record.model.begin_turn(json.dumps(resume_payload, ensure_ascii=False))
        record.steps = 0
        record.max_steps = extra_steps if extra_steps is not None else DEFAULT_RESUME_STEPS
        if timeout_seconds is not None:
            record.timeout_seconds = timeout_seconds
        record.cancel_requested = False
        record.stop_reason = ""
        record.error = ""
        record.state = "running"
        record.started_at = now
        record.updated_at = now
        record.finished_at = None
        record.last_activity_at = now
        record.last_stall_notice_at = now
        record.stall_notices_sent = 0
        record.run_generation += 1
        record.retained_bytes = record.measure()
        self.records.move_to_end(record.id)
        self._emit_record(
            record.scope.key,
            f"续跑子代理「{record.title}」（{_source_label(source)}）",
            status="running",
        )
        record.task_handle = asyncio.create_task(self._run(record, record.run_generation))
        return {
            "subagent_id": record.id,
            "title": record.title,
            "mode": "resumed",
            "state": "running",
            "max_steps": record.max_steps,
            "timeout_seconds": record.timeout_seconds,
            "notice": "子代理已在原上下文中继续运行，完成后会自动唤醒主会话。",
        }

    async def cancel(
        self,
        subagent_id: str,
        *,
        scope: ConversationScope | None = None,
        reason: str = "",
        source: Literal["main_agent", "operator", "system"] = "main_agent",
    ) -> bool:
        record = self.records.get(subagent_id)
        if record is None or (scope is not None and record.scope != scope):
            return False
        if not record.active:
            return False
        record.cancel_requested = True
        record.stop_reason = "cancelled"
        if reason.strip():
            record.error = reason.strip()
        elif source == "operator":
            record.error = "管理员已在控制台手动停止"
        elif source == "main_agent":
            record.error = "主会话已停止该子代理"
        task = record.task_handle
        watchdog = record.watchdog_handle
        if watchdog is not None:
            watchdog.cancel()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # 若任务在进入协程体前就被取消，兜底收尾状态。
        if record.active:
            record.state = "cancelled"
            record.inbox.clear()
            record.finished_at = time.time()
            record.updated_at = record.finished_at
            record.current_tool = ""
            record.current_tool_started_at = None
            if record.model is not None:
                record.model.end_turn("cancelled")
                record.retained_bytes = record.measure()
            self._emit_record(
                record.scope.key, f"子代理「{record.title}」已停止", status="cancelled"
            )
            await self._trim()
        return True

    def _get(self, subagent_id: str, *, scope: ConversationScope | None = None) -> SubagentRecord:
        record = self.records.get(subagent_id)
        if record is None or (scope is not None and record.scope != scope):
            raise SubagentNotFound("未找到属于当前会话的该子代理（可能不存在或已被回收）")
        return record

    def _policy_alive(self, record: SubagentRecord) -> str | None:
        if self.closed or record.cancel_requested:
            return "cancelled"
        store = self.get_store()
        if not store.settings.conversation(record.scope).enabled:
            return "所属会话已禁用"
        if self.get_parent_session_id is not None and record.parent_session_id:
            if self.get_parent_session_id(record.scope.key) != record.parent_session_id:
                return "所属主会话已重置"
        spawn_tool = self.registry.tools.get("subagent__spawn")
        if spawn_tool is not None and not self.registry.permitted(record.scope, spawn_tool)[0]:
            return "当前会话已关闭子代理权限"
        return None

    def _child_context(self, record: SubagentRecord) -> ToolContext:
        assert record.fallback_napcat is not None
        live_napcat = _LiveNapCat(self.get_napcat, record.scope.bot_id, record.fallback_napcat)
        return ToolContext(
            scope=record.scope,
            turn_id=record.id,
            napcat=live_napcat,
            cancelled=lambda: record.cancel_requested or self.closed,
            discovered_tools=record.discovered_tools,
            read_workspace=record.read_workspace,
            write_workspace=record.write_workspace,
        )

    def _sync_media(
        self, record: SubagentRecord, child_context: ToolContext, synced_ids: set[str]
    ) -> None:
        for gid, item in child_context.generated_media.items():
            if gid in synced_ids:
                continue
            synced_ids.add(gid)
            if self.on_media is not None:
                attached = self.on_media(record.scope.key, record.parent_session_id, item)
            elif record.fallback_generated_media is not None:
                remember_generated(record.fallback_generated_media, item)
                attached = True
            else:
                attached = False
            if attached and gid not in record.generated_ids:
                record.generated_ids.append(gid)

    def _wire_session_events(self, record: SubagentRecord, session: ModelSession) -> None:
        def on_context(event: JsonObject) -> None:
            phase = event.get("phase")
            if phase == "compacting":
                record.state = "compacting"
                self._emit_record(
                    record.scope.key,
                    f"子代理「{record.title}」上下文达到阈值，正在轮内压缩",
                    status="running",
                )
            elif record.state == "compacting":
                record.state = "running"
            if phase == "compacted":
                self._emit_record(
                    record.scope.key,
                    f"子代理「{record.title}」轮内压缩完成：{event.get('before_tokens')} → "
                    f"{event.get('after_tokens')} token",
                    status="completed",
                )
            elif phase == "pruned":
                self._emit_record(
                    record.scope.key,
                    f"子代理「{record.title}」前置减压完成：{event.get('before_tokens')} → "
                    f"{event.get('after_tokens')} token",
                    status="completed",
                )
            record.updated_at = time.time()

        def on_request(event: JsonObject) -> None:
            phase = event.get("phase")
            if phase == "retry":
                record.state = "retrying"
                self._emit_record(
                    record.scope.key,
                    f"子代理「{record.title}」模型请求失败：{event.get('reason')}；"
                    f"{event.get('delay_seconds')} 秒后第 {event.get('retry')}/"
                    f"{event.get('max_retries')} 次重试",
                    status="retrying",
                )
            elif phase == "recovered":
                record.state = "running"
                self._emit_record(
                    record.scope.key,
                    f"子代理「{record.title}」模型请求已恢复（第 {event.get('retry')} 次重试）",
                    status="completed",
                )
            elif phase == "attempt" and record.state == "retrying":
                record.state = "running"
            record.updated_at = time.time()

        session.on_context_event = on_context
        session.on_request_event = on_request

    def _drain_inbox(self, record: SubagentRecord, session: ModelSession) -> bool:
        if not record.inbox:
            return False
        batch: list[JsonValue] = []
        while record.inbox:
            src, text = record.inbox.popleft()
            batch.append({"来源": _source_label(src), "内容": text})
        session.append_mid_turn_wake(
            json.dumps(
                {
                    "子代理插话": batch,
                    "说明": "主会话或管理员在运行期间发送了新指示，请立即结合当前进展优先响应或调整方向。",
                },
                ensure_ascii=False,
            )
        )
        record.last_activity_at = time.time()
        record.updated_at = record.last_activity_at
        return True

    async def _watchdog(self, record: SubagentRecord, generation: int) -> None:
        last_check_in = record.started_at
        poll = max(0.02, self.watchdog_poll_seconds)
        try:
            while record.active and record.run_generation == generation and not self.closed:
                await asyncio.sleep(poll)
                if not record.active or record.run_generation != generation or self.closed:
                    return
                now = time.time()
                if now - record.started_at >= record.timeout_seconds:
                    record.stop_reason = "timeout"
                    record.cancel_requested = True
                    if record.task_handle is not None and not record.task_handle.done():
                        record.task_handle.cancel()
                    return
                if record.check_in_seconds is not None:
                    if now - last_check_in >= record.check_in_seconds:
                        last_check_in = now
                        await self._notify_parent(record, "check_in")
                elif (
                    self.stall_notice_seconds > 0
                    and record.stall_notices_sent < MAX_STALL_NOTICES_PER_RUN
                    and now - record.last_activity_at >= self.stall_notice_seconds
                    and now - record.last_stall_notice_at >= self.stall_notice_seconds
                ):
                    record.stall_notices_sent += 1
                    record.last_stall_notice_at = now
                    await self._notify_parent(record, "stall_warning")
        except asyncio.CancelledError:
            return

    def _format_wakeup(self, record: SubagentRecord, event_type: str) -> str:
        now = time.time()
        elapsed = record.elapsed(now)
        labels = {
            "completed": "已完成",
            "step_limit": "已达步数上限并汇总",
            "timeout": "运行超时",
            "failed": "运行失败",
            "check_in": "定时进度巡检",
            "stall_warning": "停滞告警（疑似卡顿）",
        }
        header = f"[子代理 {record.id}（{record.title}）{labels.get(event_type, event_type)}]"
        meta_parts = [
            f"状态：{record.state}",
            f"步数：{record.steps}/{record.max_steps}（累计 {record.total_steps}）",
            f"耗时：{elapsed} 秒",
        ]
        if record.generated_ids:
            meta_parts.append("生成媒体：" + "、".join(record.generated_ids))
        lines = [header, "｜".join(meta_parts)]
        if event_type in ("check_in", "stall_warning"):
            idle = record.idle_seconds(now)
            active_op = record.current_tool or "等待模型响应"
            lines.append(
                f"距上次进展已过 {idle} 秒｜当前执行：{active_op}｜最近工具：{record.last_tool or '无'}"
            )
            lines.append(
                "提示：如需排查是否偏离目标或卡死，可调用 `subagent__inspect` 查看其会话，"
                "必要时用 `subagent__send` 纠偏或 `subagent__cancel` 终止；若一切正常且暂无需回复群友，可本轮不发言继续挂起。"
            )
            return "\n".join(lines)
        if event_type in ("timeout", "failed"):
            lines.append(f"原因：{record.error or '未知错误'}")
            lines.append(
                "提示：可调用 `subagent__inspect` 查看中断前已获取的线索，"
                "必要时调用 `subagent__send` 在原上下文中续跑，或直接向群友说明当前情况。"
            )
            return "\n".join(lines)
        report = record.report.strip() or "（子代理未输出正文报告）"
        if len(report) > MAX_INLINE_REPORT_CHARS:
            preview = report[:MAX_INLINE_REPORT_CHARS]
            lines.append(f"汇报内容：\n{preview}")
            lines.append(
                f"[报告共 {len(report)} 字，已截断前 {MAX_INLINE_REPORT_CHARS} 字；"
                f'如需后续全文，请调用 subagent__inspect(subagent_id="{record.id}", '
                f"report_offset={MAX_INLINE_REPORT_CHARS})]"
            )
        else:
            lines.append(f"汇报内容：\n{report}")
        if event_type == "step_limit":
            lines.append(
                "提示：子代理已达到本轮步数上限并输出阶段性成果；如需继续深入，可调用 `subagent__send` 在原上下文续跑。"
            )
        return "\n".join(lines)

    async def _notify_parent(self, record: SubagentRecord, event_type: str) -> None:
        if self.on_wake is None or self.closed:
            return
        record.event_seq += 1
        event_id = f"{record.id}:{record.run_generation}:{record.event_seq}:{event_type}"
        text = self._format_wakeup(record, event_type)
        try:
            await self.on_wake(
                record.scope,
                record.parent_session_id,
                event_id,
                event_type,
                record.title,
                text,
            )
        except Exception as exc:
            logger.error(
                "subagent_wake_failed id=%s scope=%s error_type=%s",
                record.id,
                record.scope.key,
                type(exc).__name__,
                exc_info=True,
            )

    async def _run(self, record: SubagentRecord, generation: int) -> None:
        session = record.model
        assert session is not None
        child_context = self._child_context(record)
        synced_media: set[str] = set(record.generated_ids)
        self._wire_session_events(record, session)
        record.watchdog_handle = asyncio.create_task(self._watchdog(record, generation))

        outcome: SubagentState = "failed"
        interrupted_call: str | None = None
        repeat_sig: tuple[str, str, str] | None = None
        repeat_count = 0

        def record_tool_result(call: ToolCall, res: ToolResult) -> str | None:
            nonlocal repeat_sig, repeat_count
            session.result(call, res)
            record.last_tool = call.name
            record.last_activity_at = time.time()
            record.updated_at = record.last_activity_at
            self._sync_media(record, child_context, synced_media)
            if call.name == "qq__search_tools":
                return None
            canon_args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
            outcome_fp = (
                f"err:{res.error_type}:{res.message}"
                if res.status != "ok"
                else "ok:" + json.dumps(res.data, sort_keys=True, ensure_ascii=False)[:1024]
            )
            sig = (call.name, canon_args, outcome_fp)
            if sig == repeat_sig:
                repeat_count += 1
            else:
                repeat_sig, repeat_count = sig, 1
            if repeat_count in REPEAT_REMINDER_THRESHOLDS:
                return (
                    f"循环提醒：工具 {call.name} 已连续 {repeat_count} 次以相同参数返回无变化结果，"
                    "请检查已有信息、更换思路或准备收尾汇总。"
                )
            return None

        def is_stuck_repeat(call: ToolCall) -> bool:
            if call.name == "qq__search_tools" or repeat_sig is None:
                return False
            canon_args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
            return (
                repeat_sig[0] == call.name
                and repeat_sig[1] == canon_args
                and repeat_count >= REPEAT_BLOCK_THRESHOLD - 1
            )

        def update_action_state(*, wrap_up: bool = False) -> None:
            state_obj: JsonObject = {
                "subagent_id": record.id,
                "step": record.steps,
                "max_steps": record.max_steps,
                "remaining_steps": max(0, record.max_steps - record.steps),
                "elapsed_seconds": record.elapsed(),
                "timeout_seconds": record.timeout_seconds,
                "generated_ids": [gid for gid in record.generated_ids],
            }
            if wrap_up:
                state_obj["wrap_up"] = True
            session.action_state = state_obj

        try:
            finished_cleanly = False
            while record.steps < record.max_steps:
                if reason := self._policy_alive(record):
                    if reason == "cancelled":
                        outcome = "timeout" if record.stop_reason == "timeout" else "cancelled"
                    else:
                        outcome = "cancelled"
                        record.error = reason
                    finished_cleanly = True
                    break

                if self._drain_inbox(record, session):
                    repeat_sig, repeat_count = None, 0

                record.steps += 1
                record.total_steps += 1
                record.last_activity_at = time.time()
                record.updated_at = record.last_activity_at

                if record.max_steps >= 6 and record.max_steps - record.steps == 2:
                    session.add_notice(
                        f"步数提醒：距离本次运行步数上限（{record.max_steps} 步）仅剩 2 步，"
                        "请尽快收敛并整理已有事实输出最终报告。"
                    )

                update_action_state()
                defs = self.registry.definitions(child_context, tool_filter=_allowed_for_subagent)
                reply = await session.step(defs)
                record.last_activity_at = time.time()
                record.updated_at = record.last_activity_at
                record.retained_bytes = record.measure()

                if not reply.calls:
                    if not record.cancel_requested and record.inbox:
                        continue
                    record.report = reply.text.strip()
                    outcome = "completed"
                    finished_cleanly = True
                    break

                reminders: list[str] = []
                calls = reply.calls
                idx = 0
                while idx < len(calls):
                    if record.cancel_requested or self.closed:
                        raise asyncio.CancelledError
                    call = calls[idx]
                    if call.invalid:
                        if note := record_tool_result(
                            call,
                            ToolResult.failure("工具参数不是有效 JSON 对象", "InvalidArguments"),
                        ):
                            reminders.append(note)
                        idx += 1
                        continue

                    tool_def = self.registry.tools.get(call.name)
                    if call.name != "qq__search_tools" and (
                        tool_def is None or not _allowed_for_subagent(tool_def)
                    ):
                        if note := record_tool_result(
                            call,
                            ToolResult.failure(
                                "子智能体不允许调用该工具（对外发送或群管请交回主会话执行）",
                                "PermissionDenied",
                            ),
                        ):
                            reminders.append(note)
                        idx += 1
                        continue

                    if is_stuck_repeat(call):
                        blocked = ToolResult.failure(
                            "子智能体连续重复相同工具调用无进展，已拦截；请换方案或直接汇总已有发现。",
                            "RepeatedToolCallBlocked",
                        )
                        if note := record_tool_result(call, blocked):
                            reminders.append(note)
                        idx += 1
                        continue

                    if tool_def is not None and tool_def.concurrency_safe:
                        batch = [call]
                        idx += 1
                        while idx < len(calls) and len(batch) < MAX_PARALLEL_READ_TOOLS:
                            nxt = calls[idx]
                            ntool = self.registry.tools.get(nxt.name)
                            if (
                                nxt.invalid
                                or ntool is None
                                or not _allowed_for_subagent(ntool)
                                or not ntool.concurrency_safe
                                or is_stuck_repeat(nxt)
                            ):
                                break
                            batch.append(nxt)
                            idx += 1
                        record.current_tool = (
                            batch[0].name
                            if len(batch) == 1
                            else f"{batch[0].name} 等 {len(batch)} 项"
                        )
                        record.current_tool_started_at = time.time()
                        try:
                            results = await asyncio.gather(
                                *(
                                    self.registry.execute(
                                        child_context,
                                        c.name,
                                        c.arguments,
                                        tool_filter=_allowed_for_subagent,
                                    )
                                    for c in batch
                                )
                            )
                        finally:
                            record.current_tool = ""
                            record.current_tool_started_at = None
                        for c, r in zip(batch, results, strict=True):
                            if note := record_tool_result(c, r):
                                reminders.append(note)
                        record.retained_bytes = record.measure()
                        continue

                    interrupted_call = call.id
                    record.current_tool = call.name
                    record.current_tool_started_at = time.time()
                    try:
                        res = await self.registry.execute(
                            child_context,
                            call.name,
                            call.arguments,
                            tool_filter=_allowed_for_subagent,
                        )
                    finally:
                        record.current_tool = ""
                        record.current_tool_started_at = None
                        interrupted_call = None
                    if note := record_tool_result(call, res):
                        reminders.append(note)
                    record.retained_bytes = record.measure()
                    idx += 1

                for reminder in reminders:
                    session.add_notice(reminder)

            if not finished_cleanly and not record.cancel_requested and not self.closed:
                # 到达步数上限时执行一次无工具强制收尾，保留已获得的阶段性成果。
                session.add_notice(
                    f"已达到本次运行步数上限（{record.max_steps} 步），工具调用已停止。"
                    "请立即基于目前掌握的全部事实，输出结构清晰的阶段性汇总报告（注明已完成部分、关键路径/数据与未完成项）。"
                )
                update_action_state(wrap_up=True)
                wrap_reply = await session.step([])
                record.report = wrap_reply.text.strip()
                outcome = "step_limit"
        except asyncio.CancelledError:
            if record.stop_reason == "timeout":
                outcome = "timeout"
                record.error = f"子代理运行超过 {int(record.timeout_seconds)} 秒上限已自动停止"
            else:
                outcome = "cancelled"
        except (ContextLimitError, CompactionError, ProviderError) as exc:
            outcome = "failed"
            record.error = f"子代理模型请求失败：{exc}"
        except Exception as exc:
            outcome = "failed"
            record.error = f"子代理运行发生 {type(exc).__name__}"
            logger.error(
                "subagent_failed id=%s scope=%s error_type=%s",
                record.id,
                record.scope.key,
                type(exc).__name__,
                exc_info=True,
            )
        finally:
            wd = record.watchdog_handle
            record.watchdog_handle = None
            record.current_tool = ""
            record.current_tool_started_at = None
            self._sync_media(record, child_context, synced_media)
            session.end_turn(outcome, interrupted_call)
            session.on_context_event = None
            session.on_request_event = None
            record.state = outcome
            record.inbox.clear()
            record.finished_at = time.time()
            record.updated_at = record.finished_at
            record.retained_bytes = record.measure()
            record.task_handle = None
            self.records.move_to_end(record.id)
            status_text = {
                "completed": "已完成",
                "step_limit": "达步数上限并输出阶段汇报",
                "timeout": "运行超时已停止",
                "failed": "运行失败",
                "cancelled": "已停止",
            }.get(outcome, outcome)
            activity_status = (
                "error" if outcome == "failed" else "limit" if outcome == "step_limit" else outcome
            )
            self._emit_record(
                record.scope.key,
                f"子代理「{record.title}」（{record.id}）{status_text}",
                status=activity_status,
            )
            if wd is not None:
                wd.cancel()
                with suppress(asyncio.CancelledError):
                    await wd
            await self._trim()

        if (
            outcome in ("completed", "step_limit", "timeout", "failed")
            and not self.closed
            and record.run_generation == generation
        ):
            await self._notify_parent(record, outcome)

    def inspect_for_agent(
        self,
        subagent_id: str,
        scope: ConversationScope,
        *,
        tail: int = 12,
        offset: int | None = None,
        include_report: bool = True,
        report_offset: int = 0,
    ) -> JsonObject:
        record = self._get(subagent_id, scope=scope)
        raw_entries = project(record.model) if record.model is not None else []
        visible = [entry for entry in raw_entries if entry.get("kind") != "system"]
        total = len(visible)
        if offset is not None:
            start = min(offset, total)
            end = min(total, start + tail)
        else:
            end = total
            start = max(0, total - tail)

        selected: list[JsonValue] = []
        used_chars = 0
        for entry in visible[start:end]:
            compact = _compact_entry(entry, MAX_INSPECT_ENTRY_CHARS)
            size = len(json.dumps(compact, ensure_ascii=False))
            if selected and used_chars + size > MAX_INSPECT_TOTAL_CHARS:
                break
            selected.append(compact)
            used_chars += size

        payload: JsonObject = {
            **record.summary(),
            "task": _truncate_text(record.task, 2000),
            "total_entries": total,
            "returned_range": [start, start + len(selected)],
            "entries": selected,
        }
        if include_report:
            report = record.report
            chunk = report[report_offset : report_offset + MAX_INLINE_REPORT_CHARS]
            payload["report_chunk"] = chunk
            payload["report_offset"] = report_offset
            payload["report_total_chars"] = len(report)
            payload["report_truncated"] = report_offset + len(chunk) < len(report)
        return payload

    def inspect_detail(self, subagent_id: str, *, since: int | None = None) -> JsonObject | None:
        record = self.records.get(subagent_id)
        if record is None:
            return None
        return record.detail(since=since)

    def has_active(self, scope_key: str) -> bool:
        return any(rec.active and rec.scope.key == scope_key for rec in self.records.values())

    def active_count(self, scope_key: str) -> int:
        return sum(1 for rec in self.records.values() if rec.active and rec.scope.key == scope_key)

    def action_summary(self, scope: ConversationScope) -> list[JsonValue]:
        now = time.time()
        active: list[JsonValue] = [
            rec.action_summary(now)
            for rec in self.records.values()
            if rec.active and rec.scope == scope
        ]
        return active

    def list_for_scope(self, scope_key: str, *, include_finished: bool = True) -> list[JsonValue]:
        now = time.time()
        items = [
            rec
            for rec in self.records.values()
            if rec.scope.key == scope_key and (include_finished or rec.active)
        ]
        items.sort(key=lambda rec: (not rec.active, -rec.updated_at))
        return [rec.summary(now) for rec in items]

    def snapshot(self) -> list[JsonValue]:
        now = time.time()
        items = sorted(self.records.values(), key=lambda rec: (not rec.active, -rec.updated_at))
        return [rec.summary(now) for rec in items]

    async def _trim(self) -> None:
        while (
            len(self.records) > self.max_retained
            or sum(rec.retained_bytes for rec in self.records.values()) > self.max_retained_bytes
        ):
            victim_id = next((k for k, rec in self.records.items() if not rec.active), None)
            if victim_id is None:
                return
            victim = self.records.pop(victim_id)
            if victim.model is not None:
                await victim.model.close()
                victim.model = None

    async def clear_scope(self, scope_key: str) -> None:
        targets = [rec for rec in self.records.values() if rec.scope.key == scope_key]
        await asyncio.gather(
            *(self.cancel(rec.id, source="system") for rec in targets if rec.active)
        )
        for rec in targets:
            self.records.pop(rec.id, None)
            if rec.model is not None:
                await rec.model.close()
                rec.model = None

    async def close(self) -> None:
        self.closed = True
        active_ids = [rec.id for rec in self.records.values() if rec.active]
        await asyncio.gather(*(self.cancel(sub_id, source="system") for sub_id in active_ids))
        records, self.records = list(self.records.values()), OrderedDict()
        await asyncio.gather(*(rec.model.close() for rec in records if rec.model is not None))


def build_tools(manager: SubagentManager) -> list[ToolDefinition]:
    async def spawn_tool(context: ToolContext, request: SpawnSubagentInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        try:
            record = manager.spawn(context, request)
        except SubagentBusyError as exc:
            return ToolResult.failure(str(exc), "SubagentBusy")
        except ValueError as exc:
            return ToolResult.failure(str(exc), "PermissionDenied")
        return ToolResult.success(
            {
                "subagent_id": record.id,
                "title": record.title,
                "state": record.state,
                "max_steps": record.max_steps,
                "timeout_seconds": record.timeout_seconds,
                "notice": (
                    "子代理已在后台异步启动；如果当前还有其他事情（如先回复群友或并行派发其他子任务）"
                    "可继续执行，没有其他事情可直接结束本轮挂起等待。挂起期间群友 @ 你仍会正常响应，"
                    "子代理完成后系统会自动唤醒你并带回结果。"
                ),
            }
        )

    async def list_tool(context: ToolContext, request: ListSubagentsInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        items = manager.list_for_scope(context.scope.key, include_finished=request.include_finished)
        return ToolResult.success(
            {
                "subagents": items,
                "active": manager.active_count(context.scope.key),
                "total": len(items),
            }
        )

    async def inspect_tool(context: ToolContext, request: InspectSubagentInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        try:
            data = manager.inspect_for_agent(
                request.subagent_id.strip(),
                context.scope,
                tail=request.tail,
                offset=request.offset,
                include_report=request.include_report,
                report_offset=request.report_offset,
            )
        except SubagentNotFound as exc:
            return ToolResult.failure(str(exc), "SubagentNotFound")
        return ToolResult.success(scrub(data))

    async def send_tool(context: ToolContext, request: SendSubagentInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        try:
            data = await manager.send_message(
                request.subagent_id.strip(),
                request.message,
                source="main_agent",
                scope=context.scope,
                extra_steps=request.extra_steps,
                timeout_seconds=request.timeout_seconds,
            )
        except SubagentNotFound as exc:
            return ToolResult.failure(str(exc), "SubagentNotFound")
        except SubagentBusyError as exc:
            return ToolResult.failure(str(exc), "SubagentBusy")
        return ToolResult.success(data)

    async def cancel_tool(context: ToolContext, request: CancelSubagentInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        sub_id = request.subagent_id.strip()
        cancelled = await manager.cancel(
            sub_id, scope=context.scope, reason=request.reason, source="main_agent"
        )
        if not cancelled:
            return ToolResult.failure(
                "未找到属于当前会话且正在运行的该子代理（可能已结束、已取消或不存在）",
                "SubagentNotFound",
            )
        return ToolResult.success({"subagent_id": sub_id, "cancelled": True, "state": "cancelled"})

    return [
        ToolDefinition(
            "subagent__spawn",
            (
                "异步启动一个独立上下文的后台子智能体去执行繁重的多步调查、源码分析、长文档阅读、"
                "MCP 批量检索或工作区计算，立即返回 subagent_id 而不阻塞当前对话。"
                "启动后可继续处理其他事项，无其他事项时直接结束本轮挂起，子代理完成后会自动唤醒你带回汇报；"
                "挂起期间若有人 @ 你会正常先处理对方消息。"
            ),
            "subagent",
            model_binding(SpawnSubagentInput, spawn_tool),
            title="启动后台子代理",
        ),
        ToolDefinition(
            "subagent__list",
            "查看当前会话下运行中及近期结束的后台子代理状态（步数进度、已耗时、距上次进展秒数、当前工具与上下文用量）。",
            "subagent",
            model_binding(ListSubagentsInput, list_tool),
            concurrency_safe=True,
            title="查看子代理状态",
        ),
        ToolDefinition(
            "subagent__inspect",
            "只读查看指定子代理的会话轨迹与进展（支持尾部截取、分页及分段读取超长报告），用于判断子代理是否偏离方向或卡死。",
            "subagent",
            model_binding(InspectSubagentInput, inspect_tool),
            concurrency_safe=True,
            title="阅读子代理会话",
        ),
        ToolDefinition(
            "subagent__send",
            "向指定子代理发送指示：运行中时将在下一步边界实时注入纠偏/补充说明；已结束或达步数上限时将在原上下文中续跑并在完成后再次唤醒你。",
            "subagent",
            model_binding(SendSubagentInput, send_tool),
            title="纠偏或续跑子代理",
        ),
        ToolDefinition(
            "subagent__cancel",
            "立即停止当前会话下正在运行的指定子代理及其关联工作区命令，防止卡死或偏离任务继续消耗资源。",
            "subagent",
            model_binding(CancelSubagentInput, cancel_tool),
            title="停止子代理",
        ),
    ]
