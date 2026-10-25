"""有界内存对话循环；无业务存档、重启恢复或自动发送。"""

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from .configuration import ConfigStore
from .contracts import (
    ConversationScope,
    JsonObject,
    Message,
    NapCatConnection,
    ToolContext,
    ToolResult,
)
from .prompts import compose_prompt
from .providers import MediaInput, ModelSession
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


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
        if not await self.should_wake(message):
            return
        key = message.scope.key
        settings = self.store.settings.runtime
        now = time.monotonic()
        while self.seen and (
            next(iter(self.seen.values())) < now - 300 or len(self.seen) >= 10_000
        ):
            self.seen.popitem(last=False)
        identity = (key, message.message_id)
        if identity in self.seen:
            return
        if key not in self.workers and len(self.workers) >= settings.max_pending_conversations:
            self.record(key, "overload", "等待会话已满，本次唤醒未接入")
            return
        queue = self.pending.setdefault(key, [])
        if any(item.message_id == message.message_id for item in queue):
            return
        if len(queue) >= settings.max_pending_messages:
            self.record(key, "overload", "当前会话等待消息已满，本次唤醒未接入")
            return
        queue.append(message)
        self.seen[identity] = now
        if key not in self.workers:
            self.stopping.discard(key)
            self.turns[key] = {"scope_key": key, "state": "queued", "started_at": time.time()}
            self.workers[key] = asyncio.create_task(self._worker(key))

    async def _worker(self, key: str) -> None:
        try:
            while self.pending.get(key):
                async with self.semaphore:
                    batch = self.pending.pop(key, [])
                    if batch:
                        await self._turn(batch)
        except asyncio.CancelledError:
            self.record(
                key, "turn", "本轮已停止；已提交的外部操作不会被自动撤销", status="cancelled"
            )
        except Exception as exc:
            self.record(key, "error", f"会话连接或运行失败：{type(exc).__name__}", status="error")
        finally:
            self.pending.pop(key, None)
            self.turns.pop(key, None)
            self.workers.pop(key, None)
            self.stopping.discard(key)

    async def _turn(self, batch: list[Message]) -> None:
        current = batch[-1]
        scope = current.scope
        key = scope.key
        config = self.store.settings
        provider = config.providers.get(config.active_provider)
        if provider is None or not provider.model:
            self.record(key, "error", "未配置有效模型，不启动推理")
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
            denied = self.registry.authorize(scope, tool, {"path": path})
            if denied:
                raise ValueError(denied.message)
            return await self.read_workspace(path)

        async def write_workspace(path: str, data: bytes) -> None:
            tool = self.registry.tools.get("sandbox__write_file")
            if not tool or not self.write_workspace:
                raise ValueError("当前群未开放共享工作区写入能力")
            denied = self.registry.authorize(scope, tool, {"path": path, "overwrite": False})
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
        self.registry.initial(context)
        session: ModelSession | None = None
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
                unique = {m.message_id: m for m in history}
                unique.update({m.message_id: m for m in batch})
                entries = [m.model_dump(mode="json") for m in unique.values()]
                while (
                    len(entries) > len(batch)
                    and len(json.dumps(entries, ensure_ascii=False))
                    > config.runtime.context_char_limit
                ):
                    entries.pop(0)
                conversation = json.dumps(
                    {
                        "来源会话": scope.model_dump(),
                        "历史限制": history_error,
                        "对话资料": entries,
                        "当前唤醒消息": [m.message_id for m in batch],
                    },
                    ensure_ascii=False,
                )
                if len(conversation) > config.runtime.context_char_limit:
                    self.record(key, "error", "本轮输入超过上下文上限，未截断消息结构或执行操作")
                    return
                persona = self.store.resolve_file(config.persona_file).read_text(encoding="utf-8")
                knowledge = [
                    self.store.resolve_file(path).read_text(encoding="utf-8")
                    for path in config.knowledge_files
                ]
                # 知识库作为资料进入 user 内容，不能提升为系统级指令。
                conversation = json.dumps(
                    {"知识库资料": knowledge, "会话资料": json.loads(conversation)},
                    ensure_ascii=False,
                )
                session = self.session_factory(provider, compose_prompt(persona), conversation)
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
                                {
                                    "message_id": message.message_id,
                                    "indexes": None,
                                    "include_forwards": False,
                                },
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
                        session.items.append(
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "media_errors": [
                                            *errors,
                                            *(
                                                media_errors
                                                if isinstance(media_errors, list)
                                                else []
                                            ),
                                        ]
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                executed: dict[str, tuple[str, JsonObject, ToolResult]] = {}
                for round_number in range(config.runtime.max_model_rounds):
                    self.turns[key]["rounds"] = round_number + 1
                    definitions = self.registry.definitions(context)
                    if session.context_chars(definitions) > config.runtime.context_char_limit:
                        self.record(
                            key,
                            "turn",
                            "完整上下文已达到字符上限，已停止；未截断工具调用配对",
                            status="limit",
                        )
                        return
                    reply = await session.step(definitions)
                    if not reply.calls:
                        self.record(
                            key,
                            "turn",
                            "本轮结束" if context.sent_messages else "本轮静默结束；普通输出未发送",
                            status="completed",
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
                            result = await self.registry.execute(context, call.name, call.arguments)
                        session.result(call, result, config.runtime.tool_result_char_limit)
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
                self.record(key, "turn", "达到本轮模型往返上限，已停止", status="limit")
        except TimeoutError:
            self.record(key, "turn", "本轮超时，已停止；已提交动作状态以回执为准", status="timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 只记录异常类型，避免将模型正文、密钥、工具原始结果写入日志。
            self.record(key, "error", f"本轮发生 {type(exc).__name__}，已停止", status="error")
            logger.error("turn_failed scope=%s error_type=%s", key, type(exc).__name__)
        finally:
            if session:
                await session.close()

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

    async def close(self) -> None:
        await asyncio.gather(*(self.cancel(key) for key in list(self.workers)))

    def snapshot(self) -> JsonObject:
        return {"turns": list(self.turns.values()), "activity": list(self.activity)}
