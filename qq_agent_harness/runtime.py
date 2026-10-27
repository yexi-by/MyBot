"""有界内存对话循环；无业务存档、重启恢复或自动发送。"""

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol

from .configuration import ConfigStore
from .contracts import (
    JSON_OBJECT,
    ConversationScope,
    JsonObject,
    JsonValue,
    Message,
    NapCatConnection,
    ToolContext,
    ToolResult,
    UnknownOperation,
)
from .napcat.arguments import ReadMedia
from .prompts import compose_prompt
from .providers import (
    CompactionError,
    ContextLimitError,
    MediaInput,
    ModelRequestLimit,
    ModelSession,
    ProviderError,
)
from .registry import ToolRegistry
from .session_view import project, scrub

logger = logging.getLogger(__name__)

# 空闲会话只按容量回收，不因等待下一条消息而超时销毁。
MAX_MESSAGE_IDS = 2048
# 闲时压缩按固定间隔检查；同时进行的压缩数量有上限，不占用会话并发名额。
IDLE_SCAN_SECONDS = 15
MAX_IDLE_COMPACTIONS = 2


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
    unknown_effects: list[UnknownOperation] = field(default_factory=list)
    knowledge: list[tuple[str, str]] = field(default_factory=list)
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
                    "unknown": [
                        [
                            operation.id,
                            operation.tool,
                            operation.arguments,
                            operation.target.model_dump(mode="json"),
                        ]
                        for operation in self.unknown_effects
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )


class Hub(Protocol):
    def bot_connection(self, bot_id: str) -> NapCatConnection: ...
    async def get_history(self, scope: ConversationScope, count: int) -> list[Message]: ...


SessionFactory = Callable[..., ModelSession]


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
        self.idle_interrupts: set[str] = set()
        self.rejected_messages = 0
        self.idle_task: asyncio.Task[None] | None = None
        self.closing = False
        self.read_workspace: Callable[[str], Awaitable[bytes]] | None = None
        self.write_workspace: Callable[[str, bytes], Awaitable[None]] | None = None
        self.read_media: (
            Callable[[list[JsonObject], NapCatConnection], Awaitable[JsonObject]] | None
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

    async def receive(self, message: Message) -> None:
        if self.closing or not await self.should_wake(message):
            return
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
            return
        if key not in self.workers and len(self.workers) >= settings.max_pending_conversations:
            self.rejected_messages += 1
            self.record(key, "overload", "等待会话已满，本次唤醒未接入")
            return
        queue = self.pending.setdefault(key, [])
        if any(item.message_id == message.message_id for item in queue):
            return
        if len(queue) >= settings.max_pending_messages:
            self.rejected_messages += 1
            self.record(key, "overload", "当前会话等待消息已满，本次唤醒未接入")
            return
        queue.append(message)
        self.seen[identity] = now
        if key in self.compacting and key not in self.idle_interrupts:
            self.idle_interrupts.add(key)
            self.workers[key].cancel()
        if key not in self.workers:
            self.stopping.discard(key)
            self.turns[key] = {"scope_key": key, "state": "queued", "started_at": time.time()}
            self.workers[key] = asyncio.create_task(self._worker(key))

    async def _worker(self, key: str, *, idle: bool = False) -> None:
        # 闲时压缩占用处理位置；新唤醒只中断一次压缩，保留原文后接入排队消息。
        compacting = idle
        try:
            if idle:
                try:
                    await self._compact_idle(key)
                except asyncio.CancelledError:
                    if key not in self.idle_interrupts or key in self.stopping or self.closing:
                        raise
                    self.idle_interrupts.discard(key)
                    if task := asyncio.current_task():
                        task.uncancel()
                    self.record(
                        key,
                        "compaction",
                        "新消息到达，已中断闲时压缩并保留原上下文",
                        status="cancelled",
                    )
            compacting = False
            while self.pending.get(key):
                async with self.semaphore:
                    batch = self.pending.pop(key, [])
                    if batch:
                        await self._turn(batch)
        except asyncio.CancelledError:
            if compacting:
                self.record(key, "compaction", "闲时压缩已停止，原上下文保留", status="cancelled")
            else:
                self.record(
                    key, "turn", "本轮已停止；已提交的外部操作不会被自动撤销", status="cancelled"
                )
        except Exception as exc:
            self.record(key, "error", f"会话连接或运行失败：{type(exc).__name__}", status="error")
            logger.error(
                "worker_failed scope=%s error_type=%s", key, type(exc).__name__, exc_info=True
            )
        finally:
            self.pending.pop(key, None)
            self.turns.pop(key, None)
            self.workers.pop(key, None)
            self.stopping.discard(key)
            self.compacting.discard(key)
            self.idle_interrupts.discard(key)
            await self._trim_sessions()

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
            try:
                async with asyncio.timeout(config.runtime.turn_timeout):
                    before, after = await session.compact_idle(
                        endpoint, request_limit=config.runtime.max_model_rounds
                    )
            except TimeoutError:
                self.record(key, "compaction", "闲时压缩超时，原上下文保留", status="timeout")
                return
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
            retained.idle_tokens = after
            retained.retained_bytes = retained.measure()
            self.record(
                key,
                "compaction",
                f"闲时压缩完成（{endpoint.model}）：{before} → {after} token",
                status="completed",
            )
        finally:
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

        async def write_workspace(path: str, data: bytes) -> None:
            tool = self.registry.tools.get("sandbox__write_file")
            if not tool or not self.write_workspace:
                raise ValueError("当前群未开放共享工作区写入能力")
            denied = self.registry.authorize(
                scope, tool, {"path": path, "overwrite": False, "append": False}
            )
            if denied:
                raise ValueError(denied.message)
            if any(
                key == "content" or key.startswith("content.")
                for key in self.registry.policy(scope).parameters.get(tool.name, {})
            ):
                raise ValueError("二进制导入不能满足该群对文本写入内容的限制")
            await self.write_workspace(path, data)

        context = ToolContext(
            scope,
            turn_id,
            self.hub.bot_connection(scope.bot_id),
            cancelled=lambda: key in self.stopping,
            read_workspace=read_workspace,
            write_workspace=write_workspace,
        )
        session: ModelSession | None = None
        retained = self.sessions.get(key)
        if retained:
            context.unknown_effects = retained.unknown_effects
        executed: dict[str, tuple[str, JsonObject, ToolResult]] = {}
        outcome = "error"
        interrupted_call: str | None = None

        def update_actions() -> None:
            if session is None:
                return
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
                "max_sends_per_turn": self.registry.policy(scope).max_sends_per_turn,
                "executed_calls": [
                    {"call_id": call_id, "tool": name, "status": result.status}
                    for call_id, (name, _arguments, result) in executed.items()
                ],
                "unknown_operations": [
                    {
                        "id": operation.id,
                        "tool": operation.tool,
                        "target": operation.target.model_dump(mode="json"),
                    }
                    for operation in context.unknown_effects
                ],
            }

        def context_event(event: JsonObject) -> None:
            phase = event.get("phase")
            self.turns[key]["state"] = "compacting" if phase == "compacting" else "running"
            if session is not None:
                # 请求发出时原地递增计数；共享元数据使等待响应期间的 UI 也能看到真实次数。
                self.turns[key]["context"] = session.context_state
            if isinstance(count := event.get("request_count"), int):
                self.turns[key]["rounds"] = count
            if phase == "compacting":
                self.record(
                    key, "compaction", "上下文达到预算，正在强制压缩旧资料", status="running"
                )
            elif phase == "compacted":
                self.record(
                    key,
                    "compaction",
                    f"上下文已压缩：{event.get('before_tokens')} → {event.get('after_tokens')} token；继续本轮",
                    status="completed",
                )
            elif phase == "compaction_skipped":
                self.record(
                    key, "compaction", "压缩未完成，当前输入仍在窗口内，继续本轮", status="warning"
                )

        try:
            async with asyncio.timeout(config.runtime.turn_timeout):
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
                    history_error = (
                        "NapCat 历史暂不可用；仅依据本轮收到的消息，不能假设此前没有对话。"
                    )
                conversation = json.dumps(
                    {
                        "来源会话": scope.model_dump(),
                        "历史限制": history_error,
                        "当前唤醒消息": [m.model_dump(mode="json") for m in batch],
                    },
                    ensure_ascii=False,
                )
                persona = self.store.resolve_file(config.persona_file).read_text(encoding="utf-8")
                if retained is None or retained.model is None:
                    session = self.session_factory(endpoint, compose_prompt(persona), conversation)
                    if retained is None:
                        retained = ConversationSession(session, scope)
                        self.sessions[key] = retained
                    else:
                        retained.model = session
                    context.unknown_effects = retained.unknown_effects
                else:
                    session = retained.model
                    await session.configure(endpoint, compose_prompt(persona))
                    session.begin_turn(conversation)
                self.sessions.move_to_end(key)
                self.turns[key]["session_id"] = retained.session_id
                session.request_limit = config.runtime.max_model_rounds
                session.on_context_event = context_event
                # 资料与当前输入分开：旧历史和知识可以压缩，当前消息及安全规则保持完整。
                knowledge = [
                    (path, self.store.resolve_file(path).read_text(encoding="utf-8"))
                    for path in config.knowledge_files
                ]
                if knowledge != retained.knowledge:
                    session.add_background(
                        json.dumps(
                            {
                                "当前知识库": [
                                    {"文件": path, "资料": text} for path, text in knowledge
                                ],
                                "说明": "当前版本替代此前知识库资料；移除的文件不再作为当前知识依据。",
                            },
                            ensure_ascii=False,
                        ),
                        "knowledge",
                    )
                    retained.knowledge = knowledge
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
                session.media_limit = config.napcat.media_max_bytes
                media_tool = self.registry.tools.get("qq__read_media")
                if self.read_media and media_tool and self.registry.permitted(scope, media_tool)[0]:
                    raw = await self.read_media(
                        [
                            s
                            for message in batch
                            if not self.registry.authorize(
                                scope,
                                media_tool,
                                JSON_OBJECT.validate_python(
                                    ReadMedia(
                                        message_id=message.message_id, include_forwards=False
                                    ).model_dump(mode="json")
                                ),
                            )
                            for s in message.segments
                            if s.get("type") in ("image", "record", "video")
                        ],
                        context.napcat,
                    )
                    media: list[MediaInput] = []
                    media_items = raw.get("media")
                    for item in media_items if isinstance(media_items, list) else []:
                        if isinstance(item, dict) and item.get("type") in (
                            "image",
                            "audio",
                            "video",
                        ):
                            kind = item["type"]
                            if kind in ("image", "audio", "video"):
                                media.append(
                                    MediaInput(
                                        kind,
                                        str(item.get("mime_type", "")),
                                        str(item.get("base64", "")),
                                    )
                                )
                    errors = session.add_media(media)
                    media_errors = raw.get("errors")
                    if errors or media_errors:
                        session.add_notice(
                            json.dumps(
                                {
                                    "media_errors": [
                                        *errors,
                                        *(media_errors if isinstance(media_errors, list) else []),
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        )
                for round_number in range(config.runtime.max_model_rounds):
                    definitions = self.registry.definitions(context)
                    update_actions()
                    self.turns[key]["rounds"] = round_number + 1
                    reply = await session.step(definitions)
                    self.turns[key]["context"] = session.context_state
                    self.turns[key]["rounds"] = session.request_count
                    if not reply.calls:
                        outcome = "completed" if context.sent_messages else "silent"
                        self.record(
                            key,
                            "turn",
                            "本轮结束" if context.sent_messages else "本轮静默结束；普通输出未发送",
                            status=outcome,
                        )
                        return
                    # 每个调用完整回填一次，保持协议配对；不把正文补发至QQ。
                    for call in reply.calls:
                        if call.id in executed:
                            old_name, old_args, result = executed[call.id]
                            if (old_name, old_args) != (call.name, call.arguments):
                                result = ToolResult.failure(
                                    "模型复用了工具调用 ID 且参数不同", "DuplicateCall"
                                )
                        elif call.invalid:
                            result = ToolResult.failure(
                                "工具参数不是有效 JSON 对象", "InvalidArguments"
                            )
                        else:
                            interrupted_call = call.id
                            result = await self.registry.execute(context, call.name, call.arguments)
                        session.result(call, result)
                        interrupted_call = None
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
                outcome = "limit"
                self.record(key, "turn", "达到本轮模型请求上限，已停止", status=outcome)
        except ModelRequestLimit:
            outcome = "limit"
            self.record(key, "turn", "达到模型或压缩请求上限，已停止", status="limit")
        except ContextLimitError as exc:
            outcome = "limit"
            self.record(key, "turn", str(exc), status="limit")
        except CompactionError as exc:
            self.record(key, "compaction", str(exc), status="error")
        except ProviderError as exc:
            # ProviderError 的消息由协议层生成固定脱敏文本，不携带上游响应正文。
            self.record(key, "error", str(exc), status="error")
        except TimeoutError:
            outcome = "timeout"
            self.record(key, "turn", "本轮超时，已停止；已提交动作状态以回执为准", status="timeout")
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
                update_actions()
                session.end_turn(outcome, interrupted_call)
                retained.updated_at = time.time()
                retained.outcome = outcome
                retained.retained_bytes = retained.measure()
                retained.idle_compacted = False
                retained.idle_tokens = session.idle_pressure()
                self.sessions.move_to_end(key)

    async def cancel(self, key: str) -> bool:
        task = self.workers.get(key)
        if task is None:
            return False
        self.stopping.add(key)
        self.pending.pop(key, None)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    async def reset(self, key: str, *, stop: bool) -> str:
        """清空会话的模型上下文；结果未知动作和已处理唤醒的防重记录保留。"""
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
        # 清空已读历史，下次唤醒像新会话一样补入近期 QQ 历史。
        retained.message_ids.clear()
        retained.outcome = "reset"
        retained.updated_at = time.time()
        retained.retained_bytes = retained.measure()
        self.record(key, "reset", "会话上下文已在控制台重置；防重记录保留", status="completed")
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

    def resolve_operation(self, key: str, operation_id: str, executed: bool) -> None:
        retained = self.sessions.get(key)
        if retained is None:
            raise KeyError(key)
        if key in self.workers:
            raise SessionBusy("请先停止当前轮次，再核实未知动作")
        operation = next(
            (item for item in retained.unknown_effects if item.id == operation_id), None
        )
        if operation is None:
            raise KeyError(operation_id)
        retained.unknown_effects.remove(operation)
        if retained.model is not None:
            retained.model.add_notice(
                json.dumps(
                    {
                        "控制台核实动作": {
                            "id": operation.id,
                            "tool": operation.tool,
                            "arguments": operation.arguments,
                            "target": operation.target.model_dump(mode="json"),
                            "executed": executed,
                        },
                        "说明": "操作者已核实该动作，未知状态已解除；不会自动重放。",
                    },
                    ensure_ascii=False,
                )
            )
        retained.retained_bytes = retained.measure()
        self.record(
            key,
            "resolution",
            "未知动作已核实为已执行" if executed else "未知动作已核实为未执行，可重新调用",
            status="completed",
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
            "unknown_operations": [
                scrub(
                    {
                        "id": operation.id,
                        "tool": operation.tool,
                        "arguments": operation.arguments,
                        "target": operation.target.model_dump(mode="json"),
                    }
                )
                for operation in retained.unknown_effects
            ],
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
