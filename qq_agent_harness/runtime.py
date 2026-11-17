"""有界内存对话循环；支持同批只读并发、轮中新消息实时注入、无进展循环防卫与按需技能目录。"""

import asyncio
import base64
import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Protocol

from .configuration import ConfigStore
from .contracts import (
    ConversationScope,
    GeneratedMedia,
    JsonObject,
    JsonValue,
    Message,
    NapCatConnection,
    OrderedSet,
    ToolContext,
    ToolResult,
)
from .napcat.arguments import ReadMedia
from .napcat.media import MediaBatch
from .prompts import SILENT_RESPONSE, compose_prompt
from .providers import (
    CompactionError,
    ContextLimitError,
    MediaInput,
    ModelSession,
    ProviderError,
    ToolCall,
)
from .registry import ToolRegistry
from .session_view import project, scrub
from .settings import HarnessSettings
from .skills import SKILL_TOOL_NAME, discover_skills

logger = logging.getLogger(__name__)

# 空闲会话只按容量回收，不因等待下一条消息而超时销毁。
MAX_MESSAGE_IDS = 2048
# 闲时压缩按固定间隔检查；同时进行的压缩数量有上限，不占用会话并发名额。
IDLE_SCAN_SECONDS = 15
MAX_IDLE_COMPACTIONS = 2
MAX_PARALLEL_READ_TOOLS = 8
REPEAT_REMINDER_THRESHOLDS = (3, 5, 8)
REPEAT_ERROR_BLOCK_THRESHOLD = 8


class SessionBusy(RuntimeError):
    """会话仍在处理或停止期间又被唤醒，不能重置上下文。"""


@dataclass
class ConversationSession:
    # None 表示上下文已在控制台重置，下次唤醒时重新建立；防重记录仍随对象保留。
    model: ModelSession | None
    scope: ConversationScope
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    message_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    wake_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    discovered_tools: OrderedSet = field(default_factory=OrderedSet)
    generated_media: OrderedDict[str, GeneratedMedia] = field(default_factory=OrderedDict)
    knowledge: list[tuple[str, str]] = field(default_factory=list)
    skills_catalog: list[tuple[str, str]] = field(default_factory=list)
    updated_at: float = 0
    outcome: str = ""
    retained_bytes: int = 0
    # 每段闲置只压缩一次，下一轮结束后重新计时；用量在轮次结束时估算一次。
    idle_compacted: bool = False
    idle_tokens: int = 0

    def remember(self, identifiers: list[str], *, wake: bool = False) -> None:
        for values in (self.message_ids, self.wake_ids) if wake else (self.message_ids,):
            for identifier in identifiers:
                values[identifier] = None
                values.move_to_end(identifier)
            while len(values) > MAX_MESSAGE_IDS:
                values.popitem(last=False)

    def measure(self) -> int:
        return len(
            json.dumps(
                {
                    "items": self.model.items if self.model else [],
                    "knowledge": self.knowledge,
                    "skills": self.skills_catalog,
                },
                ensure_ascii=False,
            ).encode("utf-8")
        ) + sum(
            len(item.content) + len(item.id) + len(item.mime_type)
            for item in self.generated_media.values()
        )


class Hub(Protocol):
    def bot_connection(self, bot_id: str) -> NapCatConnection: ...
    async def get_history(self, scope: ConversationScope, count: int) -> list[Message]: ...


SessionFactory = Callable[..., ModelSession]
type ExecutedCalls = dict[str, tuple[str, JsonObject, ToolResult]]


class AgentRuntime:
    def __init__(
        self,
        store: ConfigStore,
        registry: ToolRegistry,
        hub: Hub,
        *,
        session_factory: SessionFactory = ModelSession,
    ) -> None:
        self.store = store
        self.registry = registry
        self.hub = hub
        self.session_factory = session_factory
        self.pending: dict[str, list[Message]] = {}
        self.workers: dict[str, asyncio.Task[None]] = {}
        self.turns: dict[str, JsonObject] = {}
        self.activity: deque[JsonObject] = deque(maxlen=store.settings.runtime.activity_limit)
        self.semaphore = asyncio.Semaphore(store.settings.runtime.concurrent_conversations)
        self.stopping: set[str] = set()
        self.seen: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.sessions: OrderedDict[str, ConversationSession] = OrderedDict()
        self.compacting: set[str] = set()
        self.started_workers: set[str] = set()
        self.idle_interrupts: set[str] = set()
        self.rejected_messages = 0
        self.idle_task: asyncio.Task[None] | None = None
        self.closing = False
        self.read_workspace: Callable[[str], Awaitable[bytes]] | None = None
        self.write_workspace: Callable[..., Awaitable[None]] | None = None
        self.read_media: (
            Callable[[list[JsonObject], NapCatConnection], Awaitable[MediaBatch]] | None
        ) = None

    def record(self, scope_key: str, kind: str, message: str, **extra: str) -> None:
        self.activity.append(
            {
                "id": uuid.uuid4().hex,
                "time": time.time(),
                "scope_key": scope_key,
                "kind": kind,
                "message": message,
                **extra,
            }
        )
        logger.info(
            "harness_event kind=%s scope=%s status=%s", kind, scope_key, extra.get("status", "")
        )

    def _model_request_event(self, key: str, event: JsonObject) -> None:
        phase = event.get("phase")
        kind = event.get("request_kind")
        label = {
            "model": "模型请求",
            "compaction": "轮内压缩请求",
            "idle_compaction": "闲时压缩请求",
        }.get(str(kind), "模型请求")
        level = (
            logging.ERROR
            if phase == "failed"
            else logging.WARNING
            if phase == "retry"
            else logging.INFO
        )
        logger.log(level, "model_request_%s", phase, extra={**event, "scope_key": key})
        if turn := self.turns.get(key):
            turn["state"] = (
                "retrying" if phase == "retry" else "running" if kind == "model" else "compacting"
            )
            retained = self.sessions.get(key)
            if kind != "idle_compaction" and retained and retained.model:
                turn["rounds"] = retained.model.request_count
        if phase == "retry":
            self.record(
                key,
                "model",
                f"{label}失败：{event.get('reason')}；{event.get('delay_seconds')} 秒后进行第 "
                f"{event.get('retry')}/{event.get('max_retries')} 次重试",
                status="retrying",
            )
        elif phase == "recovered":
            self.record(
                key,
                "model",
                f"{label}已恢复，第 {event.get('retry')} 次重试成功",
                status="completed",
            )

    async def should_wake(self, message: Message) -> bool:
        config = self.store.settings.conversation(message.scope)
        if not config.enabled or message.sender_id == message.scope.bot_id:
            return False
        if message.scope.kind == "private":
            return True
        if any(word and word in message.text for word in config.keywords):
            return True
        for segment in message.segments:
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            if segment.get("type") == "at" and str(data.get("qq")) == message.scope.bot_id:
                return True
            if segment.get("type") == "reply":
                try:
                    response = await self.hub.bot_connection(message.scope.bot_id).call(
                        "get_msg", {"message_id": str(data.get("id", ""))}
                    )
                    if isinstance(response, dict):
                        sender = response.get("sender")
                        if (
                            str(response.get("group_id")) == message.scope.peer_id
                            and isinstance(sender, dict)
                            and str(sender.get("user_id")) == message.scope.bot_id
                        ):
                            return True
                except TimeoutError, ConnectionError, ValueError, OSError:
                    self.record(message.scope.key, "wake", "无法验证被引用消息的归属")
        return False

    def _enqueue_message(self, message: Message) -> bool:
        key = message.scope.key
        settings = self.store.settings.runtime
        now = time.monotonic()
        while self.seen and (
            next(iter(self.seen.values())) < now - 300 or len(self.seen) >= 10_000
        ):
            self.seen.popitem(last=False)
        identity = (key, message.message_id)
        retained = self.sessions.get(key)
        if identity in self.seen or retained and message.message_id in retained.wake_ids:
            return False
        if key not in self.workers and len(self.workers) >= settings.max_pending_conversations:
            self.rejected_messages += 1
            self.record(key, "overload", "等待会话已满，本次唤醒未接入")
            return False
        queue = self.pending.setdefault(key, [])
        if any(item.message_id == message.message_id for item in queue):
            return False
        if len(queue) >= settings.max_pending_messages:
            self.rejected_messages += 1
            self.record(key, "overload", "当前会话等待消息已满，本次唤醒未接入")
            return False
        queue.append(message)
        self.seen[identity] = now
        if key in self.compacting and key not in self.idle_interrupts:
            self.idle_interrupts.add(key)
            if key in self.started_workers:
                self.workers[key].cancel()
        if key not in self.workers:
            self.stopping.discard(key)
            self.turns[key] = {"scope_key": key, "state": "queued", "started_at": time.time()}
            self.workers[key] = asyncio.create_task(self._worker(key))
        return True

    async def receive(self, message: Message) -> None:
        if self.closing or not await self.should_wake(message):
            return
        self._enqueue_message(message)

    async def wake_timer(self, scope: ConversationScope, timer_id: str, note: str) -> None:
        if self.closing or not self.store.settings.conversation(scope).enabled:
            return
        timer_msg = Message(
            scope=scope,
            message_id=f"timer:{timer_id}",
            sender_id=scope.bot_id,
            sender_name="[系统定时提醒]",
            time=int(time.time()),
            segments=[
                {
                    "type": "text",
                    "data": {"text": f"[系统定时提醒 {timer_id} 已到期] 提醒事项：{note}"},
                }
            ],
        )
        if self._enqueue_message(timer_msg):
            self.record(scope.key, "wake", f"定时提醒已到期唤醒（{timer_id}）", status="ok")

    def _interrupt_idle(self, key: str) -> None:
        self.idle_interrupts.discard(key)
        self.compacting.discard(key)
        if retained := self.sessions.get(key):
            retained.idle_compacted = False
        self.record(
            key,
            "compaction",
            "新消息到达，已中断闲时压缩并保留原上下文",
            status="cancelled",
        )

    async def _cleanup_worker(self, key: str) -> None:
        self.pending.pop(key, None)
        self.turns.pop(key, None)
        self.workers.pop(key, None)
        self.started_workers.discard(key)
        self.stopping.discard(key)
        self.compacting.discard(key)
        self.idle_interrupts.discard(key)
        await self._trim_sessions()

    async def _worker(self, key: str, *, idle: bool = False) -> None:
        # 闲时压缩占用处理位置；新唤醒只中断一次压缩，保留原文后接入排队消息。
        compacting = idle
        self.started_workers.add(key)
        try:
            if idle:
                if key in self.idle_interrupts and key not in self.stopping and not self.closing:
                    self._interrupt_idle(key)
                else:
                    try:
                        await self._compact_idle(key)
                    except asyncio.CancelledError:
                        if key not in self.idle_interrupts or key in self.stopping or self.closing:
                            raise
                        if task := asyncio.current_task():
                            task.uncancel()
                        self._interrupt_idle(key)
            compacting = False
            while self.pending.get(key):
                turn = self.turns.get(key)
                if turn is None or turn.get("idle") or turn.get("state") != "queued":
                    self.turns[key] = {
                        "scope_key": key,
                        "state": "queued",
                        "started_at": time.time(),
                    }
                async with self.semaphore:
                    batch = self.pending.pop(key, [])
                    if batch:
                        await self._turn(batch)
        except asyncio.CancelledError:
            if compacting:
                self.record(key, "compaction", "闲时压缩已停止，原上下文保留", status="cancelled")
            else:
                self.record(key, "turn", "本轮已手动停止", status="cancelled")
        except Exception as exc:
            self.record(key, "error", f"会话连接或运行失败：{type(exc).__name__}", status="error")
            logger.error(
                "worker_failed scope=%s error_type=%s", key, type(exc).__name__, exc_info=True
            )
        finally:
            await self._cleanup_worker(key)

    async def _trim_sessions(self) -> None:
        while (
            len(self.sessions) > self.store.settings.runtime.max_retained_sessions
            or sum(value.retained_bytes for value in self.sessions.values())
            > self.store.settings.runtime.max_retained_bytes
        ):
            idle = next((key for key in self.sessions if key not in self.workers), None)
            if idle is None:
                return
            retained = self.sessions.pop(idle)
            if retained.model:
                await retained.model.close()
            self.record(idle, "session", "内存容量达到上限，已回收最久未使用的空闲会话")

    def start(self) -> None:
        """启动闲时压缩检查；由应用生命周期调用。"""
        if self.idle_task is None:
            self.idle_task = asyncio.create_task(self._idle_loop())

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(IDLE_SCAN_SECONDS)
            self.compact_idle_sessions()

    def compact_idle_sessions(self, now: float | None = None) -> list[str]:
        """为闲置够久且上下文够大的会话启动压缩；返回本次启动的会话键。"""
        if self.closing:
            return []
        now = time.time() if now is None else now
        started: list[str] = []
        for key, retained in self.sessions.items():
            if len(self.compacting) >= MAX_IDLE_COMPACTIONS:
                break
            if key in self.workers or retained.model is None or retained.idle_compacted:
                continue
            settings = self.store.settings.conversation(retained.scope).idle_compaction
            if (
                not settings.enabled
                or now - retained.updated_at < settings.after_minutes * 60
                or retained.idle_tokens <= 0
                or retained.idle_tokens < settings.min_tokens
            ):
                continue
            retained.idle_compacted = True
            self.compacting.add(key)
            self.turns[key] = {
                "scope_key": key,
                "state": "compacting",
                "started_at": time.time(),
                "idle": True,
                "context": retained.model.context_state,
            }
            self.workers[key] = asyncio.create_task(self._worker(key, idle=True))
            started.append(key)
        return started

    async def _compact_idle(self, key: str) -> None:
        """压缩失败、超时都保留原上下文，随后照常处理等待中的唤醒。"""
        retained = self.sessions.get(key)
        session: ModelSession | None = None
        try:
            if retained is None or (session := retained.model) is None:
                return
            config = self.store.settings
            endpoint = config.compaction_endpoint(retained.scope)
            if endpoint is None:
                self.record(key, "compaction", "未配置有效模型，跳过闲时压缩", status="error")
                return
            self.record(
                key,
                "compaction",
                f"会话已闲置，正在用 {endpoint.model} 压缩上下文",
                status="running",
            )
            session.on_request_event = lambda event: self._model_request_event(key, event)
            try:
                before, after = await session.compact_idle(endpoint)
            except ProviderError as exc:
                # 消息由协议层生成固定脱敏文本，不含上游响应正文。
                self.record(key, "compaction", f"闲时压缩失败，原上下文保留：{exc}", status="error")
                return
            except Exception as exc:
                self.record(
                    key,
                    "compaction",
                    f"闲时压缩发生 {type(exc).__name__}，原上下文保留",
                    status="error",
                )
                logger.error(
                    "idle_compaction_failed scope=%s error_type=%s", key, type(exc).__name__
                )
                return
            if retained.knowledge and not session.contains("knowledge"):
                # 知识库原文此前已在轮内压缩时并入摘要；下次唤醒重新补入当前原文。
                retained.knowledge = []
            retained.skills_catalog = []
            retained.idle_tokens = session.idle_compactable_tokens()
            retained.retained_bytes = retained.measure()
            self.record(
                key,
                "compaction",
                f"闲时压缩完成（{endpoint.model}）：{before} → {after} token",
                status="completed",
            )
        finally:
            if session is not None:
                session.on_request_event = None
            self.compacting.discard(key)

    async def _turn(self, batch: list[Message]) -> None:
        current = batch[-1]
        scope = current.scope
        key = scope.key
        config = self.store.settings
        endpoint = config.session_endpoint(scope)
        if endpoint is None:
            self.record(key, "error", "会话未选择有效模型，不启动推理")
            return
        turn_id = uuid.uuid4().hex
        self.turns[key] = {
            "scope_key": key,
            "turn_id": turn_id,
            "state": "running",
            "started_at": time.time(),
            "rounds": 0,
        }

        context = self._tool_context(scope, turn_id)
        session: ModelSession | None = None
        retained = self.sessions.get(key)
        if retained:
            context.discovered_tools = retained.discovered_tools
            context.generated_media = retained.generated_media
        executed: ExecutedCalls = {}
        outcome = "error"
        interrupted_call: str | None = None
        repeat_sig: tuple[str, str, str] | None = None
        repeat_count = 0

        def record_completed(call: ToolCall, result: ToolResult) -> str | None:
            nonlocal repeat_sig, repeat_count
            assert session is not None
            session.result(call, result)
            executed[call.id] = (
                call.name,
                call.arguments,
                result.model_copy(
                    update={
                        "data": None,
                        "message": result.message or "该调用已执行，详细结果见先前回执",
                    }
                ),
            )
            self.record(
                key,
                "tool",
                result.message or "工具执行完成",
                tool=call.name,
                status=result.status,
            )
            if call.name == "qq__search_tools":
                return None
            canon_args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
            # 只有输入相同且结果完全停滞（或连续失败）才计入无进展重复，避免误伤步进状态工具。
            outcome_fp = (
                f"err:{result.error_type}:{result.message}"
                if result.status != "ok"
                else "ok:" + json.dumps(result.data, sort_keys=True, ensure_ascii=False)[:1024]
            )
            sig = (call.name, canon_args, outcome_fp)
            if sig == repeat_sig:
                repeat_count += 1
            else:
                repeat_sig, repeat_count = sig, 1
            if repeat_count in REPEAT_REMINDER_THRESHOLDS:
                if repeat_count == REPEAT_REMINDER_THRESHOLDS[0]:
                    return (
                        "循环提醒：你已连续多次以相同参数调用同一工具且返回结果没有变化。"
                        "请先仔细检查上一条工具结果，尝试更换参数、换用其他工具，或基于已有事实收尾。"
                    )
                preview = canon_args if len(canon_args) <= 300 else canon_args[:300] + "…"
                return (
                    f"循环警告：工具 {call.name}（参数 {preview}）已连续无进展重复调用 {repeat_count} 次。"
                    "请勿再次发出完全相同的调用，立即更换方案或结束当前任务。"
                )
            return None

        try:
            history, history_error = await self._load_history(scope, config)
            conversation = json.dumps(
                {
                    "来源会话": scope.model_dump(),
                    "历史限制": history_error,
                    "当前唤醒消息": [m.model_dump(mode="json") for m in batch],
                },
                ensure_ascii=False,
            )
            persona = self.store.resolve_file(config.conversation(scope).persona_file).read_text(
                encoding="utf-8"
            )
            if retained is None or retained.model is None:
                session = self.session_factory(endpoint, compose_prompt(persona), conversation)
                if retained is None:
                    retained = ConversationSession(session, scope)
                    self.sessions[key] = retained
                else:
                    retained.model = session
                context.discovered_tools = retained.discovered_tools
                context.generated_media = retained.generated_media
            else:
                session = retained.model
                await session.configure(endpoint, compose_prompt(persona))
                session.begin_turn(conversation)
            self.sessions.move_to_end(key)
            self.turns[key]["session_id"] = retained.session_id
            session.on_context_event = partial(self._context_event, key, session)
            session.on_request_event = lambda event: self._model_request_event(key, event)
            self._add_background(session, retained, batch, history, config)
            await self._add_media(session, context, batch, config)
            round_number = 0
            while True:
                if not context.cancelled() and self.pending.get(key):
                    mid_batch = self.pending.pop(key, [])
                    if mid_batch:
                        retained.remember([m.message_id for m in mid_batch], wake=True)
                        session.append_mid_turn_wake(
                            json.dumps(
                                {
                                    "来源会话": scope.model_dump(),
                                    "说明": "本轮处理期间收到的新消息，请结合当前进展一并回应或按最新要求调整行动。",
                                    "当前唤醒消息": [m.model_dump(mode="json") for m in mid_batch],
                                },
                                ensure_ascii=False,
                            )
                        )
                        await self._add_media(session, context, mid_batch, self.store.settings)
                        repeat_sig, repeat_count = None, 0
                        self.record(
                            key, "wake", f"轮中实时并入 {len(mid_batch)} 条新消息", status="ok"
                        )

                round_number += 1
                definitions = self.registry.definitions(context)
                self._update_actions(session, context, executed)
                self.turns[key]["rounds"] = max(session.request_count, round_number)
                reply = await session.step(definitions)
                self.turns[key]["context"] = session.context_state
                self.turns[key]["rounds"] = session.request_count
                if not reply.calls:
                    if not context.cancelled() and self.pending.get(key):
                        # 收尾瞬间若恰好有新消息抵达，直接衔接进同一轮，避免漏看最新发言。
                        continue
                    outcome = "completed" if context.sent_messages else "silent"
                    ending = (
                        "本轮结束"
                        if context.sent_messages
                        else "模型明确选择本轮不发言"
                        if reply.text.strip() == SILENT_RESPONSE
                        else "本轮结束（未发送消息）"
                    )
                    self.record(
                        key,
                        "turn",
                        ending,
                        status=outcome,
                    )
                    return

                # 每个调用完整回填一次：连续的纯只读工具并发执行并按原序回填，副作用工具独占执行。
                reminders: list[str] = []
                calls = reply.calls
                idx = 0
                while idx < len(calls):
                    call = calls[idx]
                    if call.id in executed:
                        old_name, old_args, prev_result = executed[call.id]
                        result = (
                            ToolResult.failure("模型复用了工具调用 ID 且参数不同", "DuplicateCall")
                            if (old_name, old_args) != (call.name, call.arguments)
                            else prev_result
                        )
                        if note := record_completed(call, result):
                            reminders.append(note)
                        idx += 1
                        continue
                    if call.invalid:
                        result = ToolResult.failure(
                            "工具参数不是有效 JSON 对象", "InvalidArguments"
                        )
                        if note := record_completed(call, result):
                            reminders.append(note)
                        idx += 1
                        continue

                    tool_def = self.registry.tools.get(call.name)
                    canon_args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
                    is_stuck_error = (
                        repeat_sig is not None
                        and repeat_sig[0] == call.name
                        and repeat_sig[1] == canon_args
                        and repeat_sig[2].startswith("err:")
                        and repeat_count >= REPEAT_ERROR_BLOCK_THRESHOLD - 1
                    )
                    if is_stuck_error:
                        result = ToolResult.failure(
                            f"已拦截连续第 {repeat_count + 1} 次完全相同的失败调用；请修正参数、改换方法或直接结束。",
                            "RepeatedToolCallBlocked",
                        )
                        if note := record_completed(call, result):
                            reminders.append(note)
                        idx += 1
                        continue

                    if tool_def is not None and tool_def.concurrency_safe:
                        parallel_batch = [call]
                        idx += 1
                        while idx < len(calls) and len(parallel_batch) < MAX_PARALLEL_READ_TOOLS:
                            nxt = calls[idx]
                            if nxt.id in executed or nxt.invalid:
                                break
                            ntool = self.registry.tools.get(nxt.name)
                            if ntool is None or not ntool.concurrency_safe:
                                break
                            parallel_batch.append(nxt)
                            idx += 1
                        if len(parallel_batch) == 1:
                            single = parallel_batch[0]
                            res = await self.registry.execute(
                                context, single.name, single.arguments
                            )
                            if note := record_completed(single, res):
                                reminders.append(note)
                        else:
                            batch_results = await asyncio.gather(
                                *(
                                    self.registry.execute(context, item.name, item.arguments)
                                    for item in parallel_batch
                                )
                            )
                            for item, res in zip(parallel_batch, batch_results, strict=True):
                                if note := record_completed(item, res):
                                    reminders.append(note)
                        continue

                    interrupted_call = call.id
                    result = await self.registry.execute(context, call.name, call.arguments)
                    interrupted_call = None
                    if note := record_completed(call, result):
                        reminders.append(note)
                    idx += 1

                for reminder in reminders:
                    session.add_notice(reminder)
        except ContextLimitError as exc:
            outcome = "limit"
            self.record(key, "turn", str(exc), status="limit")
        except CompactionError as exc:
            self.record(key, "compaction", str(exc), status="error")
        except ProviderError as exc:
            # ProviderError 的消息由协议层生成固定脱敏文本，不携带上游响应正文。
            self.record(key, "error", str(exc), status="error")
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            # 只记录异常类型，避免将模型正文、密钥、工具原始结果写入日志。
            self.record(key, "error", f"本轮发生 {type(exc).__name__}，已停止", status="error")
            logger.error(
                "turn_failed scope=%s error_type=%s", key, type(exc).__name__, exc_info=True
            )
        finally:
            if session is not None and retained is not None:
                self._update_actions(session, context, executed)
                session.end_turn(outcome, interrupted_call)
                retained.updated_at = time.time()
                retained.outcome = outcome
                retained.retained_bytes = retained.measure()
                retained.idle_compacted = False
                retained.idle_tokens = session.idle_compactable_tokens()
                self.sessions.move_to_end(key)

    def _tool_context(self, scope: ConversationScope, turn_id: str) -> ToolContext:
        key = scope.key

        async def read_workspace(path: str) -> bytes:
            tool = self.registry.tools.get("sandbox__read_file")
            if not tool or not self.read_workspace:
                raise ValueError("当前群未开放共享工作区读取能力")
            denied = self.registry.authorize(
                scope,
                tool,
                {
                    "path": path,
                    "offset": 0,
                    "limit": self.store.settings.sandbox.transfer_max_bytes,
                },
            )
            if denied:
                raise ValueError(denied.message)
            return await self.read_workspace(path)

        async def write_workspace(path: str, data: bytes, *, overwrite: bool = False) -> None:
            tool = self.registry.tools.get("sandbox__write_file")
            if not tool or not self.write_workspace:
                raise ValueError("当前群未开放共享工作区写入能力")
            denied = self.registry.authorize(
                scope, tool, {"path": path, "overwrite": overwrite, "append": False}
            )
            if denied:
                raise ValueError(denied.message)
            if any(
                k == "content" or k.startswith("content.")
                for k in self.registry.policy(scope).parameters.get(tool.name, {})
            ):
                raise ValueError("二进制导入不能满足该群对文本写入内容的限制")
            if overwrite:
                await self.write_workspace(path, data, overwrite=True)
            else:
                await self.write_workspace(path, data)

        return ToolContext(
            scope,
            turn_id,
            self.hub.bot_connection(scope.bot_id),
            cancelled=lambda: key in self.stopping,
            read_workspace=read_workspace,
            write_workspace=write_workspace,
        )

    def _update_actions(
        self, session: ModelSession, context: ToolContext, executed: ExecutedCalls
    ) -> None:
        receipts: list[JsonValue] = []
        for receipt in context.sent_messages:
            safe_receipt = {"action": receipt.get("action"), "status": receipt.get("status")}
            if isinstance(data := receipt.get("data"), dict):
                for name in ("message_id", "res_id", "file_id"):
                    if isinstance(identifier := data.get(name), (str, int)):
                        safe_receipt[name] = str(identifier)
            receipts.append(safe_receipt)
        session.action_state = {
            "sent_receipts": receipts,
            "send_attempts": context.send_attempts,
            "executed_calls": [
                {"call_id": call_id, "tool": name, "status": result.status}
                for call_id, (name, _arguments, result) in executed.items()
            ],
        }

    def _context_event(self, key: str, session: ModelSession, event: JsonObject) -> None:
        phase = event.get("phase")
        self.turns[key]["state"] = "compacting" if phase == "compacting" else "running"
        # 请求及重试发出时原地递增计数；等待响应期间 UI 也能看到真实次数。
        self.turns[key]["context"] = session.context_state
        if isinstance(count := event.get("request_count"), int):
            self.turns[key]["rounds"] = count
        if phase == "compacting":
            self.record(key, "compaction", "上下文达到阈值，正在进行轮内压缩", status="running")
        elif phase == "pruned":
            self.record(
                key,
                "compaction",
                f"前置无损减压完成：{event.get('before_tokens')} → {event.get('after_tokens')} token",
                status="completed",
            )
        elif phase == "compacted":
            self.record(
                key,
                "compaction",
                f"轮内压缩完成：{event.get('before_tokens')} → {event.get('after_tokens')} token",
                status="completed",
            )
        elif phase == "compaction_skipped":
            self.record(
                key, "compaction", "轮内压缩未完成，当前输入仍在窗口内，继续本轮", status="warning"
            )

    async def _load_history(
        self, scope: ConversationScope, config: HarnessSettings
    ) -> tuple[list[Message], str]:
        history_error = ""
        try:
            history_tool = self.registry.tools.get("qq__get_history")
            history = (
                await self.hub.get_history(scope, config.runtime.history_count)
                if config.runtime.history_count
                and history_tool
                and not self.registry.authorize(
                    scope, history_tool, {"count": config.runtime.history_count}
                )
                else []
            )
        except TimeoutError, ConnectionError, ValueError, OSError:
            history = []
            history_error = "历史消息读取失败，本轮仅包含当前收到的消息。"
        return history, history_error

    def _add_background(
        self,
        session: ModelSession,
        retained: ConversationSession,
        batch: list[Message],
        history: list[Message],
        config: HarnessSettings,
    ) -> None:
        scope = retained.scope
        key = scope.key
        # 资料与当前输入分开：旧历史和知识可以压缩，当前消息及安全规则保持完整。
        knowledge = [
            (path, self.store.resolve_file(path).read_text(encoding="utf-8"))
            for path in config.conversation(scope).knowledge_files
        ]
        if knowledge != retained.knowledge:
            session.add_background(
                json.dumps(
                    {
                        "当前知识库": [{"文件": path, "资料": text} for path, text in knowledge],
                        "说明": "以当前版本为准。",
                    },
                    ensure_ascii=False,
                ),
                "knowledge",
            )
            retained.knowledge = knowledge

        skill_tool = self.registry.tools.get(SKILL_TOOL_NAME)
        if skill_tool is not None and self.registry.permitted(scope, skill_tool)[0]:
            skills = [(item.name, item.description) for item in discover_skills(self.store)]
            if skills and skills != retained.skills_catalog:
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
                retained.skills_catalog = skills

        current_ids = {message.message_id for message in batch}
        waiting_ids = {message.message_id for message in self.pending.get(key, [])}
        known_ids = current_ids | waiting_ids | retained.message_ids.keys()
        new_history = [
            message
            for message in {message.message_id: message for message in history}.values()
            if message.message_id not in known_ids
        ]
        if new_history:
            session.add_background(
                json.dumps(
                    {
                        "来源会话": scope.model_dump(mode="json"),
                        "历史消息": [
                            message.model_dump(mode="json", exclude={"scope"})
                            for message in new_history
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "history",
            )
            retained.remember([message.message_id for message in new_history])
        retained.remember([message.message_id for message in batch], wake=True)

    async def _add_media(
        self,
        session: ModelSession,
        context: ToolContext,
        batch: list[Message],
        config: HarnessSettings,
    ) -> None:
        session.media_limit = config.napcat.media_max_bytes
        scope = context.scope
        tool = self.registry.tools.get("qq__read_media")
        if self.read_media is None or tool is None or not self.registry.permitted(scope, tool)[0]:
            return
        segments = [
            segment
            for message in batch
            if not self.registry.authorize(
                scope,
                tool,
                ReadMedia(message_id=message.message_id, include_forwards=False).model_dump(
                    mode="json"
                ),
            )
            for segment in message.segments
            if segment.get("type") in ("image", "record", "video")
        ]
        result = await self.read_media(segments, context.napcat)
        media = [
            MediaInput(
                "audio" if asset.type == "record" else asset.type,
                asset.mime_type,
                base64.b64encode(asset.content).decode("ascii"),
            )
            for _, asset in result.media
            if asset.type != "file"
        ]
        errors = session.add_media(media)
        if errors or result.errors:
            session.add_notice(
                json.dumps(
                    {"media_errors": [*errors, *(error.public() for error in result.errors)]},
                    ensure_ascii=False,
                )
            )

    async def cancel(self, key: str) -> bool:
        task = self.workers.get(key)
        if task is None:
            return False
        was_compacting = key in self.compacting
        self.stopping.add(key)
        self.pending.pop(key, None)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # 任务在进入协程体前就被取消时，try/finally 不会执行，需在此兜底清理。
        if self.workers.get(key) is task:
            if was_compacting:
                self.record(key, "compaction", "闲时压缩已停止，原上下文保留", status="cancelled")
            else:
                self.record(key, "turn", "本轮已手动停止", status="cancelled")
            await self._cleanup_worker(key)
        return True

    async def reset(self, key: str, *, stop: bool) -> str:
        """清空会话的模型上下文；已处理唤醒的防重记录保留。"""
        retained = self.sessions.get(key)
        if retained is None:
            raise KeyError(key)
        if key in self.workers:
            if not stop:
                raise SessionBusy("会话正在处理，请先停止本轮")
            await self.cancel(key)
            if self.sessions.get(key) is not retained:
                raise KeyError(key)
            if key in self.workers:
                raise SessionBusy("停止期间会话又被唤醒，请稍后再试")
        model, retained.model = retained.model, None
        retained.session_id = uuid.uuid4().hex
        retained.knowledge = []
        retained.skills_catalog = []
        retained.discovered_tools.clear()
        retained.generated_media.clear()
        # 清空已读历史，下次唤醒像新会话一样补入近期 QQ 历史。
        retained.message_ids.clear()
        retained.outcome = "reset"
        retained.updated_at = time.time()
        retained.retained_bytes = retained.measure()
        self.record(key, "reset", "会话上下文已重置", status="completed")
        if model is not None:
            await model.close()
        return retained.session_id

    async def close(self) -> None:
        self.closing = True
        if self.idle_task is not None:
            self.idle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.idle_task
        await asyncio.gather(*(self.cancel(key) for key in list(self.workers)))
        sessions, self.sessions = self.sessions, OrderedDict()
        await asyncio.gather(
            *(retained.model.close() for retained in sessions.values() if retained.model)
        )

    def _state(self, key: str) -> str:
        turn = self.turns.get(key)
        if turn is not None:
            return str(turn.get("state", "queued"))
        return "queued" if key in self.workers else "idle"

    def _summary(self, key: str, retained: ConversationSession) -> JsonObject:
        model = retained.model
        return {
            "scope_key": key,
            "session_id": retained.session_id,
            "turns": model.turn if model else 0,
            "updated_at": retained.updated_at,
            "state": self._state(key),
            "idle": bool((turn := self.turns.get(key)) and turn.get("idle")),
            "pending": len(self.pending.get(key, [])),
            "last_outcome": retained.outcome,
            "context": model.context_state if model else {},
            "retained_bytes": retained.retained_bytes,
            "cleared": model is None,
            "model": model.settings.model if model else "",
        }

    def inspect(self, key: str, *, since: int | None, session_id: str) -> JsonObject | None:
        """控制台查看用的只读快照；同步构建，读取期间上下文不会被其他协程修改。"""
        retained = self.sessions.get(key)
        if retained is None:
            return None
        model = retained.model
        entries: JsonValue = None
        # 客户端已持有同一会话的同一版本时只返回摘要字段，避免轮询时重复传输全部条目。
        if model is None:
            entries = []
        elif session_id != retained.session_id or since != model.version:
            entries = [entry for entry in project(model)]
        detail: JsonObject = {
            **self._summary(key, retained),
            "version": model.version if model else 0,
            "pending_messages": [
                scrub(message.model_dump(mode="json", exclude={"scope"}))
                for message in self.pending.get(key, [])
            ],
            "available_tools": [name for name in self.registry.available(retained.scope)],
            "entries": entries,
        }
        return detail

    def snapshot(self) -> JsonObject:
        return {
            "turns": list(self.turns.values()),
            "activity": list(self.activity),
            "sessions": [self._summary(key, retained) for key, retained in self.sessions.items()],
            "capacity": {
                "sessions": len(self.sessions),
                "max_sessions": self.store.settings.runtime.max_retained_sessions,
                "bytes": sum(retained.retained_bytes for retained in self.sessions.values()),
                "max_bytes": self.store.settings.runtime.max_retained_bytes,
                "rejected_messages": self.rejected_messages,
            },
        }
