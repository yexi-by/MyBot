"""NapCat fake-bot 行为烟测：验证真实协议边界、发送副作用与媒体失败恢复。"""

import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import JsonValue

from qq_agent_harness.contracts import ConversationScope, JsonObject, Message, ToolContext
from qq_agent_harness.napcat import MediaReader, NapCatHub, build_tools
from qq_agent_harness.napcat.connection import (
    NapCatUnavailable,
    normalize_ids,
    parse_message,
    scoped_history,
    scoped_message,
)
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.settings import ConversationSettings, HarnessSettings, NapCatSettings, Policy

GROUP = ConversationScope(bot_id="1", kind="group", peer_id="10")


def message(message_id: str = "30", group: str = "10", sender: str = "20") -> JsonObject:
    return {
        "self_id": "1",
        "post_type": "message",
        "message_type": "group",
        "group_id": group,
        "user_id": sender,
        "message_id": message_id,
        "time": 1_800_000_000,
        "sender": {"user_id": sender, "nickname": "群友"},
        "message": [{"type": "text", "data": {"text": "原消息"}}],
    }


class Bot:
    def __init__(self, handler: Callable[[str, JsonObject], Awaitable[JsonValue]] | None = None):
        self.calls: list[tuple[str, JsonObject]] = []
        self.handler = handler

    async def call(
        self, action: str, params: JsonObject, *, timeout: float | None = None
    ) -> JsonValue:
        self.calls.append((action, params))
        if self.handler:
            return normalize_ids(await self.handler(action, params))
        if action == "get_msg":
            return message(str(params["message_id"]))
        if action in ("get_group_msg_history", "get_friend_msg_history"):
            return {"messages": [message()]}
        if action.startswith(("send_", "upload_")):
            return {"message_id": "99"}
        return None


def registry(
    bot: Bot, *, scope: ConversationScope = GROUP, max_sends: int = 5
) -> tuple[ToolRegistry, ToolContext, HarnessSettings]:
    tools = build_tools()
    policy = Policy(
        categories={tool.category: True for tool in tools}, max_sends_per_turn=max_sends
    )
    settings = HarnessSettings(
        groups={"10": ConversationSettings(enabled=True, policy=policy)},
        private=ConversationSettings(enabled=True, policy=policy),
    )
    result = ToolRegistry(lambda: settings)
    for tool in tools:
        result.add(tool)
    context = ToolContext(scope, "test-turn", bot)
    return result, context, settings


async def test_typed_send_preserves_cq_text_and_target_scope() -> None:
    bot = Bot()
    tools, context, _ = registry(bot)
    result = await tools.execute(
        context,
        "qq__send_message",
        {
            "parts": [
                {"type": "reply", "message_id": "30"},
                {"type": "at", "qq": "20"},
                {"type": "text", "text": "[CQ:at,qq=all] 这是原文"},
            ]
        },
    )
    assert result.status == "ok"
    action, params = bot.calls[-1]
    assert action == "send_group_msg"
    assert params["group_id"] == "10"
    assert params["message"] == [
        {"type": "reply", "data": {"id": "30"}},
        {"type": "at", "data": {"qq": "20"}},
        {"type": "text", "data": {"text": "[CQ:at,qq=all] 这是原文"}},
    ]
    assert context.sent_messages[0]["status"] == "ok"


async def test_wrong_group_reference_is_recoverable_and_does_not_send() -> None:
    async def remote(action: str, params: JsonObject) -> JsonValue:
        return message(str(params.get("message_id", "")), group="999")

    bot = Bot(remote)
    tools, context, _ = registry(bot)
    result = await tools.execute(
        context,
        "qq__send_message",
        {
            "parts": [
                {"type": "text", "text": "前半段"},
                {"type": "reply", "message_id": "foreign"},
            ]
        },
    )
    assert result.status == "error"
    assert all(not action.startswith("send_") for action, _ in bot.calls)


async def test_partial_send_unknown_has_receipts_and_is_not_retried() -> None:
    async def remote(action: str, params: JsonObject) -> JsonValue:
        if action == "send_group_msg":
            return {"message_id": "first"}
        if action == "upload_group_file":
            raise TimeoutError
        raise AssertionError(action)

    async def read(_: str) -> bytes:
        return b"file-data"

    bot = Bot(remote)
    tools, context, _ = registry(bot)
    context.read_workspace = read
    payload: JsonObject = {
        "parts": [
            {"type": "text", "text": "前一条已发出"},
            {"type": "file", "workspace_path": "report.txt", "name": "report.txt"},
            {"type": "text", "text": "这一条不能在未知结果后继续发"},
        ]
    }
    result = await tools.execute(context, "qq__send_message", payload)
    assert result.status == "unknown"
    assert [action for action, _ in bot.calls] == ["send_group_msg", "upload_group_file"]
    assert [item["status"] for item in context.sent_messages] == ["ok", "unknown"]
    again = await tools.execute(context, "qq__send_message", payload)
    assert again.status in ("error", "unknown")
    assert len(bot.calls) == 2


async def test_actual_batch_send_count_is_checked_before_any_effect() -> None:
    bot = Bot()
    tools, context, _ = registry(bot, max_sends=1)
    result = await tools.execute(
        context,
        "qq__send_message",
        {"parts": [{"type": "text", "text": "第一条"}, {"type": "poke", "user_id": "20"}]},
    )
    assert result.status == "error"
    assert not bot.calls


async def test_unconnected_action_is_error_not_unknown_and_group_bound_actions() -> None:
    async def disconnected(_: str, __: JsonObject) -> JsonValue:
        raise NapCatUnavailable("未连接")

    tools, context, _ = registry(Bot(disconnected))
    result = await tools.execute(context, "qq__set_group_ban", {"user_id": "20", "duration": 60})
    assert result.status == "error"
    assert result.error_type == "NapCatUnavailable"
    bot = Bot()
    tools, context, settings = registry(bot)
    normal = await tools.execute(context, "qq__set_group_ban", {"user_id": "20", "duration": 60})
    assert normal.status == "ok"
    assert bot.calls == [("set_group_ban", {"user_id": "20", "duration": 60, "group_id": "10"})]
    settings.groups["10"].policy.tools["qq__set_group_ban"] = False
    denied = await tools.execute(context, "qq__set_group_ban", {"user_id": "999", "duration": 60})
    assert denied.status == "error"
    assert len(bot.calls) == 1


async def test_history_filters_foreign_scope_and_keeps_original_ids() -> None:
    async def remote(_: str, __: JsonObject) -> JsonValue:
        own = message()
        own["message_id"] = 9007199254740993
        return {"messages": [own, message("other", group="999")]}

    messages = await scoped_history(Bot(remote), GROUP, 10)
    assert len(messages) == 1
    assert messages[0].message_id == "9007199254740993"
    assert messages[0].scope == GROUP


async def test_outbound_private_message_requires_peer_history_evidence() -> None:
    scope = ConversationScope(bot_id="1", kind="private", peer_id="20")

    async def remote(action: str, _: JsonObject) -> JsonValue:
        own: JsonObject = {
            "message_type": "private",
            "user_id": "1",
            "message_id": "out",
            "time": 1,
            "message": [],
        }
        return own if action == "get_msg" else {"messages": []}

    with pytest.raises(ValueError, match="归属"):
        await scoped_message(Bot(remote), scope, "out")


async def test_media_uses_url_first_refreshes_only_failure_and_returns_partial_errors() -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/expired":
            return httpx.Response(410)
        return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/png"})

    async def resolver(_: str) -> str:
        return "8.8.8.8"

    async def remote(action: str, _: JsonObject) -> JsonValue:
        assert action == "get_image"
        return {"base64": base64.b64encode(b"refreshed").decode(), "file_name": "image.png"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        reader = MediaReader(client=client, resolver=resolver)
        bot = Bot(remote)
        result = await reader.read_many(
            [
                {"type": "image", "data": {"url": "https://media.example/ok", "file": "first"}},
                {
                    "type": "image",
                    "data": {"url": "https://media.example/expired", "file": "second"},
                },
                {"type": "video", "data": {}},
            ],
            bot,
        )
    media = result.media
    errors = result.errors
    assert len(media) == 2
    assert len(errors) == 1
    assert bot.calls == [("get_image", {"file": "second"})]
    assert all(
        request.url.host == "8.8.8.8" and request.headers["Host"] == "media.example"
        for request in requests
    )


async def test_media_redirect_to_private_host_is_blocked_before_second_connection() -> None:
    calls: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/config"})

    async def resolver(host: str) -> str:
        if host == "127.0.0.1":
            raise ValueError("不允许内网")
        return "8.8.8.8"

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        reader = MediaReader(client=client, resolver=resolver)
        with pytest.raises(ValueError, match="内网"):
            await reader.download("https://media.example/redirect")
    assert len(calls) == 1


async def test_media_local_access_requires_explicit_root_and_prefers_existing_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.png"
    path.write_bytes(b"local-image")
    bot = Bot()
    asset = await MediaReader(local_roots=[tmp_path]).read(
        {"type": "image", "data": {"path": str(path), "url": "https://invalid.example/image"}}, bot
    )
    assert asset.content == b"local-image" and asset.source == "local"
    assert not bot.calls
    with pytest.raises(ValueError):
        await MediaReader().read({"type": "image", "data": {"path": str(path)}}, bot)


class Socket:
    def __init__(self, token: str = "test-token"):
        self.headers = {"authorization": "Bearer " + token, "x-self-id": "1"}
        self.incoming: asyncio.Queue[JsonObject | None] = asyncio.Queue()
        self.closed: int | None = None
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = code

    async def receive_json(self) -> JsonObject:
        value = await self.incoming.get()
        if value is None:
            raise WebSocketDisconnect()
        return value

    async def send_json(self, payload: JsonObject) -> None:
        await self.incoming.put(
            {"status": "ok", "retcode": 0, "echo": payload["echo"], "data": {"user_id": 1}}
        )


async def test_reverse_websocket_callback_can_call_api_without_deadlock() -> None:
    hub = NapCatHub(NapCatSettings(token="test-token", expected_bot_id="1"))
    socket = Socket()
    received: list[Message] = []
    reply: list[JsonValue] = []

    async def callback(event: Message) -> None:
        received.append(event)
        reply.append(await hub.bot_connection("1").call("get_login_info", {}))
        await socket.incoming.put(None)

    await socket.incoming.put(message(sender="1"))
    incoming = message()
    incoming["message"] = [{"type": "reply", "data": {"id": 9007199254740993}}]
    await socket.incoming.put(incoming)
    await asyncio.wait_for(hub.serve(cast(WebSocket, socket), callback), 1)
    assert [event.sender_id for event in received] == ["20"]
    assert received[0].segments == [{"type": "reply", "data": {"id": "9007199254740993"}}]
    assert reply == [{"user_id": "1"}]
    assert hub.status()["connected"] == []


async def test_reverse_websocket_rejects_wrong_token_without_accepting() -> None:
    hub = NapCatHub(NapCatSettings(token="test-token"))
    socket = Socket(token="wrong")

    async def callback(_: Message) -> None:
        raise AssertionError("unauthorized callback")

    await hub.serve(cast(WebSocket, socket), callback)
    assert socket.closed == 1008 and not socket.accepted


def test_bad_event_shape_does_not_become_an_instruction() -> None:
    raw = message()
    raw["time"] = {"role": "system"}
    assert parse_message(raw) is None


async def test_remote_only_file_falls_back_to_verified_resource_stream() -> None:
    async def remote(action: str, params: JsonObject) -> JsonValue:
        if action == "get_file":
            return {"file": "/remote/qq/video.mp4", "file_name": "video.mp4"}
        assert action == "download_file_stream"
        assert params == {"file_id": "native-resource-id", "chunk_size": 65536}
        return {"file_name": "video.mp4", "base64": base64.b64encode(b"movie").decode()}

    bot = Bot(remote)
    asset = await MediaReader().read({"type": "video", "data": {"file": "native-resource-id"}}, bot)
    assert asset.content == b"movie" and asset.source == "napcat_stream_base64"
    assert [name for name, _ in bot.calls] == ["get_file", "download_file_stream"]


async def test_record_conversion_base64_has_audio_mime_without_shared_mount() -> None:
    async def remote(action: str, params: JsonObject) -> JsonValue:
        assert action == "get_record" and params["out_format"] == "mp3"
        return {
            "file": "/remote/voice.silk.mp3",
            "file_name": "voice.silk",
            "base64": base64.b64encode(b"mp3-data").decode(),
        }

    asset = await MediaReader().read(
        {"type": "record", "data": {"file": "native-voice"}}, Bot(remote)
    )
    assert asset.content == b"mp3-data"
    assert asset.mime_type == "audio/mpeg"
    assert asset.public()["type"] == "audio"


async def test_media_count_and_batch_budget_are_explicit_partial_errors() -> None:
    async def remote(_: str, __: JsonObject) -> JsonValue:
        return {"base64": base64.b64encode(b"123456").decode(), "file_name": "a.png"}

    reader = MediaReader(NapCatSettings(media_max_bytes=10))
    result = await reader.read_many(
        [
            {"type": "text", "data": {"text": "不应触发资源下载"}},
            {"type": "image", "data": {"file": "first"}},
            {"type": "image", "data": {"file": "second"}},
        ],
        Bot(remote),
    )
    media, errors = result.media, result.errors
    assert len(media) == 1
    assert len(errors) == 1


@pytest.mark.parametrize("invalid", ["", "order", "length", "quota"])
async def test_download_stream_waits_for_complete_and_rejects_corrupt_frames(invalid: str) -> None:
    from qq_agent_harness.napcat.connection import WebSocketConnection

    socket = Socket()
    connection = WebSocketConnection(cast(WebSocket, socket), "1", 2, media_limit=10)
    # 使用真实 call 关联 echo；此测试直接驱动已经验证的 WebSocket 帧。
    task = asyncio.create_task(connection.call("download_file_stream", {"file_id": "native"}))
    handshake = await socket.incoming.get()
    assert handshake is not None
    echo = handshake["echo"]

    def feed(data: JsonObject) -> None:
        connection.receive(
            {"status": "ok", "retcode": 0, "echo": echo, "stream": "stream-action", "data": data}
        )

    feed(
        {
            "type": "stream",
            "data_type": "file_info",
            "file_name": "a.bin",
            "file_size": 100 if invalid == "quota" else 3,
        }
    )
    if invalid != "quota":
        assert not task.done()
        feed(
            {
                "type": "stream",
                "data_type": "file_chunk",
                "index": 3 if invalid == "order" else 0,
                "data": "YWJj",
                "size": 3,
            }
        )
        feed(
            {
                "type": "response",
                "data_type": "file_complete",
                "total_chunks": 1,
                "total_bytes": 4 if invalid == "length" else 3,
            }
        )
    if invalid:
        with pytest.raises(ValueError):
            await task
    else:
        assert await task == {"file_name": "a.bin", "file_size": "3", "base64": "YWJj"}


async def test_nested_forward_paths_preserve_tree_and_reject_cycles_or_other_scope() -> None:
    async def remote(action: str, params: JsonObject) -> JsonValue:
        if action == "get_msg":
            raw = message()
            raw["message"] = [{"type": "forward", "data": {"id": "outer"}}]
            return raw
        assert action == "get_forward_msg"
        forward_id = params["message_id"]
        if forward_id == "outer":
            return {
                "messages": [
                    {
                        "sender": {"user_id": "20"},
                        "content": [
                            {"type": "text", "data": {"text": "保留外层"}},
                            {"type": "forward", "data": {"id": "inner"}},
                        ],
                    }
                ]
            }
        assert forward_id == "inner"
        return {
            "messages": [
                {
                    "content": [
                        {"type": "image", "data": {"file": "image-id"}},
                        {"type": "forward", "data": {"id": "outer"}},
                    ]
                }
            ]
        }

    bot = Bot(remote)
    tools, context, _ = registry(bot)
    result = await tools.execute(context, "qq__get_forward_messages", {"message_id": "30"})
    assert result.status == "ok" and isinstance(result.data, dict)
    assert result.data["forward_paths"] == [["outer", "inner"]]
    assert [
        params["message_id"] for action, params in bot.calls if action == "get_forward_msg"
    ] == ["outer"]
    assert "保留外层" in str(result.data["messages"])
    nested = await tools.execute(
        context,
        "qq__get_forward_messages",
        {"message_id": "30", "forward_path": ["outer", "inner"]},
    )
    assert nested.status == "ok" and isinstance(nested.data, dict)
    assert "image-id" in str(nested.data["messages"])
    assert nested.data["forward_paths"] == []
    for path in (["other"], ["outer", "inner", "outer"]):
        assert (
            await tools.execute(
                context,
                "qq__get_forward_messages",
                {"message_id": "30", "forward_path": [item for item in path]},
            )
        ).status == "error"


def test_action_coverage_points_to_existing_registered_tools() -> None:
    from qq_agent_harness.napcat import action_coverage

    coverage = action_coverage()
    names = {tool.name for tool in build_tools()}
    rows = coverage["actions"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        if row["status"] in ("implemented", "adapted"):
            assert row["tool"] in names
        assert row["reason"] and row["source"]


async def test_revoking_group_policy_after_first_receipt_blocks_remaining_parts() -> None:
    settings: HarnessSettings

    async def remote(action: str, _: JsonObject) -> JsonValue:
        assert action == "send_group_msg"
        settings.groups["10"].policy.tools["qq__send_message"] = False
        return {"message_id": "first"}

    bot = Bot(remote)
    tools, context, settings = registry(bot)
    result = await tools.execute(
        context,
        "qq__send_message",
        {
            "parts": [
                {"type": "text", "text": "首条"},
                {"type": "poke", "user_id": "20"},
            ]
        },
    )
    assert result.status == "error"
    assert [action for action, _ in bot.calls] == ["send_group_msg"]
    assert context.sent_messages[0]["data"] == {"message_id": "first"}
