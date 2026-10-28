"""OneBot 反向 WebSocket。连接只负责协议关联，不落盘、不重试动作。"""

import asyncio
import base64
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import JsonValue

from ..contracts import JSON_OBJECT, ConversationScope, JsonObject, Message
from ..settings import NapCatSettings

logger = logging.getLogger(__name__)
ID_KEYS = {"id", "qq", "uid", "uin"}


class NapCatError(ValueError):
    def __init__(self, message: str, code: int | str = -1):
        super().__init__(message)
        self.code = code


class NapCatUnavailable(ConnectionError):
    def __init__(self, message: str, *, submitted: bool = False):
        super().__init__(message)
        self.submitted = submitted


@dataclass
class DownloadBuffer:
    limit: int
    content: bytearray = field(default_factory=bytearray)
    chunks: int = 0
    file_name: str = ""

    def receive(self, data: JsonObject) -> JsonObject | None:
        kind = data.get("data_type")
        if kind == "file_info":
            size = data.get("file_size")
            if not isinstance(size, (int, float)) or size < 0 or size > self.limit:
                raise NapCatError("远端流媒体超过大小限制或文件信息无效", "MediaSizeLimit")
            self.file_name = str(data.get("file_name", ""))
            return None
        if kind == "file_chunk":
            encoded = data.get("data")
            if (
                not isinstance(encoded, str)
                or len(encoded) > min(self.limit, 1_048_576) * 4 // 3 + 16
            ):
                raise NapCatError("远端流分块格式或大小无效", "InvalidStream")
            if data.get("index") != self.chunks:
                raise NapCatError("远端流分块顺序不连续", "InvalidStream")
            chunk = base64.b64decode(encoded, validate=True)
            if data.get("size") != len(chunk) or len(self.content) + len(chunk) > self.limit:
                raise NapCatError("远端流媒体长度无效或超过上限", "MediaSizeLimit")
            self.content.extend(chunk)
            self.chunks += 1
            return None
        if kind == "file_complete" and data.get("type") == "response":
            if data.get("total_chunks") != self.chunks or data.get("total_bytes") != len(
                self.content
            ):
                raise NapCatError("远端流媒体未完整接收", "IncompleteStream")
            return {
                "file_name": self.file_name,
                "file_size": str(len(self.content)),
                "base64": base64.b64encode(self.content).decode("ascii"),
            }
        raise NapCatError("不支持的 NapCat 下载流响应", "InvalidStream")


def normalize_ids(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [normalize_ids(item) for item in value]
    if isinstance(value, dict):
        return {
            key: str(item)
            if (key in ID_KEYS or key.endswith(("_id", "_uin")))
            and isinstance(item, (str, int))
            and not isinstance(item, bool)
            else normalize_ids(item)
            for key, item in value.items()
        }
    return value


def object_value(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise NapCatError("NapCat 返回了非对象结果", "InvalidResponse")
    return value


def parse_message(
    payload: JsonObject, bound_scope: ConversationScope | None = None
) -> Message | None:
    kind = payload.get("message_type")
    if kind not in ("group", "private"):
        return None
    sender = payload.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    sender_id = str(payload.get("user_id", sender.get("user_id", "")))
    bot_id = str(payload.get("self_id", bound_scope.bot_id if bound_scope else ""))
    message_id = payload.get("message_id")
    if not bot_id or message_id is None or not sender_id:
        return None
    message_kind: Literal["group", "private"] = "group" if kind == "group" else "private"
    if bound_scope:
        if bot_id != bound_scope.bot_id or message_kind != bound_scope.kind:
            return None
        group_id = payload.get("group_id")
        if kind == "group" and group_id is not None and str(group_id) != bound_scope.peer_id:
            return None
        if kind == "private" and sender_id not in (bound_scope.peer_id, bot_id):
            return None
        scope = bound_scope
    else:
        peer = (
            payload.get("group_id")
            if kind == "group"
            else payload.get("target_id")
            if sender_id == bot_id
            else sender_id
        )
        if peer is None:
            return None
        scope = ConversationScope(bot_id=bot_id, kind=message_kind, peer_id=str(peer))
    raw_segments = payload.get("message", [])
    segments: list[JsonObject] = []
    if isinstance(raw_segments, str):
        segments = [{"type": "text", "data": {"text": raw_segments}}]
    elif isinstance(raw_segments, list):
        for segment in raw_segments:
            if isinstance(segment, dict):
                segments.append(segment)
    raw_time = payload.get("time", 0)
    if not isinstance(raw_time, (str, int, float)) or isinstance(raw_time, bool):
        return None
    try:
        timestamp = int(raw_time)
    except ValueError, OverflowError:
        return None
    return Message(
        scope=scope,
        message_id=str(message_id),
        sender_id=sender_id,
        sender_name=str(sender.get("card") or sender.get("nickname") or sender_id),
        time=timestamp,
        segments=segments,
    )


class WebSocketConnection:
    def __init__(
        self, websocket: WebSocket, bot_id: str, timeout: float, media_limit: int = 50_000_000
    ):
        self.websocket = websocket
        self.bot_id = bot_id
        self.timeout = timeout
        self.pending: dict[str, asyncio.Future[JsonValue]] = {}
        self.downloads: dict[str, DownloadBuffer] = {}
        self.media_limit = media_limit
        self.send_lock = asyncio.Lock()
        self.closed = False

    async def call(
        self, action: str, params: JsonObject, *, timeout: float | None = None
    ) -> JsonValue:
        if self.closed:
            raise NapCatUnavailable("NapCat 连接已断开，动作未提交")
        echo = uuid4().hex
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        self.pending[echo] = future
        if action in (
            "download_file_stream",
            "download_file_record_stream",
            "download_file_image_stream",
        ):
            self.downloads[echo] = DownloadBuffer(self.media_limit)
        try:
            async with self.send_lock:
                try:
                    await self.websocket.send_json(
                        {"action": action, "params": params, "echo": echo}
                    )
                except (RuntimeError, WebSocketDisconnect) as exc:
                    raise NapCatUnavailable(
                        "NapCat 提交动作时断开，结果未知", submitted=True
                    ) from exc
            # 超时和断线都不能推断带副作用的动作未生效。
            deadline = self.timeout if timeout is None else timeout
            return await asyncio.wait_for(future, timeout=deadline)
        finally:
            self.pending.pop(echo, None)
            self.downloads.pop(echo, None)

    def receive(self, payload: JsonObject) -> bool:
        echo = payload.get("echo")
        if not isinstance(echo, str) or echo not in self.pending:
            return False
        future = self.pending[echo]
        if future.done():
            return True
        if payload.get("status") == "ok" and payload.get("retcode") in (0, "0", None):
            download = self.downloads.get(echo)
            if download:
                try:
                    result = download.receive(object_value(payload.get("data")))
                    if result is not None:
                        future.set_result(result)
                except ValueError as exc:
                    future.set_exception(exc)
            else:
                future.set_result(normalize_ids(payload.get("data")))
        else:
            # 不把任意原始响应/参数/凭据写日志。
            raw_code = payload.get("retcode", -1)
            code: int | str = raw_code if isinstance(raw_code, (str, int)) else -1
            future.set_exception(
                NapCatError(
                    str(payload.get("message") or payload.get("wording") or "NapCat 动作失败"), code
                )
            )
        return True

    def disconnect(self) -> None:
        self.closed = True
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    NapCatUnavailable(
                        "NapCat 在等待回执时断开，已提交动作的结果未知", submitted=True
                    )
                )


class NapCatHub:
    def __init__(self, settings: NapCatSettings | None = None):
        self.settings = settings or NapCatSettings()
        self.connections: dict[str, WebSocketConnection] = {}
        self.dropped_events = 0

    def bot_connection(self, bot_id: str) -> WebSocketConnection:
        connection = self.connections.get(bot_id)
        if connection is None or connection.closed:
            raise NapCatUnavailable("机器人尚未连接 NapCat")
        return connection

    def status(self) -> JsonObject:
        return {
            "connected": list(self.connections),
            "baseline_version": "v4.18.41",
            "dropped_events": self.dropped_events,
        }

    async def get_history(self, scope: ConversationScope, count: int) -> list[Message]:
        return await scoped_history(self.bot_connection(scope.bot_id), scope, count)

    async def serve(
        self, websocket: WebSocket, on_message: Callable[[Message], Awaitable[None]]
    ) -> None:
        expected = self.settings.token
        token = websocket.headers.get("authorization", "")
        token = token.removeprefix("Bearer ")
        if not expected or not secrets.compare_digest(token, expected):
            await websocket.close(code=1008, reason="NapCat token 未配置或不匹配")
            return
        await websocket.accept()
        connection: WebSocketConnection | None = None
        consumer: asyncio.Task[None] | None = None
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=self.settings.event_queue_size)

        async def consume() -> None:
            while True:
                message = await queue.get()
                try:
                    await on_message(message)
                except Exception:
                    # 回调代码缺陷记录堆栈，但不会阻塞协议回执读取。
                    logger.exception(
                        "napcat_message_dispatch_failed", extra={"bot_id": message.scope.bot_id}
                    )
                finally:
                    queue.task_done()

        try:
            consumer = asyncio.create_task(consume())
            while True:
                payload = JSON_OBJECT.validate_python(await websocket.receive_json())
                if connection and connection.receive(payload):
                    continue
                raw_bot = payload.get("self_id", websocket.headers.get("x-self-id", ""))
                bot_id = str(raw_bot or "")
                if not bot_id:
                    continue
                if self.settings.expected_bot_id and bot_id != self.settings.expected_bot_id:
                    await websocket.close(code=1008, reason="机器人账号不匹配")
                    return
                if connection is None:
                    previous = self.connections.get(bot_id)
                    connection = WebSocketConnection(
                        websocket,
                        bot_id,
                        self.settings.action_timeout,
                        self.settings.media_max_bytes,
                    )
                    self.connections[bot_id] = connection
                    if previous:
                        previous.disconnect()
                        with suppress(RuntimeError, WebSocketDisconnect):
                            await previous.websocket.close(code=1012)
                elif connection.bot_id != bot_id:
                    await websocket.close(code=1008, reason="连接中的机器人身份发生变化")
                    return
                if payload.get("post_type") != "message":
                    continue
                message = parse_message(object_value(normalize_ids(payload)))
                if message is None or message.sender_id == bot_id:
                    continue
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    self.dropped_events += 1
                    logger.warning("napcat_event_queue_full", extra={"bot_id": bot_id})
        except WebSocketDisconnect:
            pass
        finally:
            if connection:
                connection.disconnect()
                if self.connections.get(connection.bot_id) is connection:
                    del self.connections[connection.bot_id]
            if consumer:
                consumer.cancel()
                with suppress(asyncio.CancelledError):
                    await consumer

    async def close(self) -> None:
        for connection in list(self.connections.values()):
            connection.disconnect()
            with suppress(RuntimeError, WebSocketDisconnect):
                await connection.websocket.close(code=1001)
        self.connections.clear()


async def scoped_history(
    connection: object, scope: ConversationScope, count: int, message_seq: str | None = None
) -> list[Message]:
    from typing import cast

    from ..contracts import NapCatConnection

    client = cast(NapCatConnection, connection)
    if count <= 0:
        return []
    params: JsonObject = {"count": count, "reverse_order": False}
    params["group_id" if scope.kind == "group" else "user_id"] = scope.peer_id
    if message_seq:
        params["message_seq"] = message_seq
    data = object_value(
        await client.call(
            "get_group_msg_history" if scope.kind == "group" else "get_friend_msg_history", params
        )
    )
    values = data.get("messages", [])
    if not isinstance(values, list):
        raise NapCatError("NapCat 历史消息格式错误", "InvalidResponse")
    messages: list[Message] = []
    for raw in values:
        if isinstance(raw, dict):
            message = parse_message(raw, scope)
            if message:
                messages.append(message)
    return sorted(messages, key=lambda item: item.time)


async def scoped_message(
    connection: object, scope: ConversationScope, message_id: str
) -> JsonObject:
    from typing import cast

    from ..contracts import NapCatConnection

    client = cast(NapCatConnection, connection)
    data = object_value(await client.call("get_msg", {"message_id": message_id}))
    kind = data.get("message_type")
    if scope.kind == "group":
        if kind != "group" or str(data.get("group_id", "")) != scope.peer_id:
            raise ValueError("消息不属于当前获准操作的群")
    else:
        if kind != "private":
            raise ValueError("消息不属于当前私聊")
        sender_id = str(data.get("user_id", ""))
        if sender_id not in (scope.peer_id, scope.bot_id):
            raise ValueError("消息不属于当前私聊")
        if sender_id == scope.bot_id:
            target_id = data.get("target_id") or data.get("peer_id")
            if target_id is not None:
                if str(target_id) != scope.peer_id:
                    raise ValueError("消息不属于当前私聊")
                return data
            # bot 发出的私聊 get_msg 可能不含对端，必须通过定向历史验证归属。
            history = await scoped_history(client, scope, 300)
            if not any(item.message_id == message_id for item in history):
                raise ValueError("无法验证该私聊消息的会话归属")
    return data
