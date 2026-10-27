"""公开头像、受控图床与转发媒体的端到端回归。"""

import base64

import httpx
import pytest
from test_napcat import GROUP, Bot, message, registry

from qq_agent_harness.contracts import (
    ConversationScope,
    JsonObject,
    JsonValue,
    ToolContext,
    ToolDefinition,
    ToolResult,
    schema,
)
from qq_agent_harness.napcat.arguments import Empty, ReadMedia
from qq_agent_harness.napcat.media import MediaReader
from qq_agent_harness.napcat.media_sources import non_message_segment
from qq_agent_harness.settings import NapCatSettings

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="
)


async def public_ip(_: str) -> str:
    return "8.8.8.8"


async def test_avatar_and_album_sources_return_images_without_message_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, content=PNG, headers={"content-type": "application/octet-stream"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        reader = MediaReader(client=client, resolver=public_ip)
        monkeypatch.setattr("qq_agent_harness.napcat.tools.MediaReader", lambda _: reader)
        bot = Bot()
        tools, context, settings = registry(bot)
        settings.groups["10"].policy.cross_groups = ["11"]
        sources: list[JsonObject] = [
            {"user_id": "20"},
            {"group_avatar": True},
            {"group_avatar": True, "target_group_id": "11"},
            {"media_url": "https://gchat.qpic.cn/album/picture"},
        ]
        for source in sources:
            result = await tools.execute(context, "qq__read_media", source)
            assert result.status == "ok" and isinstance(result.data, dict)
            assert result.data["total"] == 1 and result.data["errors"] == []
            media = result.data["media"]
            assert isinstance(media, list) and len(media) == 1 and isinstance(media[0], dict)
            assert media[0]["base64"] == base64.b64encode(PNG).decode()
            assert media[0]["mime_type"] == "image/png"
        assert not bot.calls
        assert requests[0].url.params["nk"] == "20"
        assert requests[1].url.path == "/gh/10/10/640/"
        assert requests[2].url.path == "/gh/11/11/640/"
        assert requests[3].headers["host"] == "gchat.qpic.cn"
        assert all(request.url.host == "8.8.8.8" for request in requests)


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"message_id": "30", "user_id": "20"},
        {"user_id": "20", "group_avatar": True},
        {"user_id": "20&url=bad"},
        {"user_id": "20", "forward_path": ["outer"]},
        {"media_url": "https://qpic.cn.evil.example/image"},
        {"media_url": "https://name:secret@qlogo.cn/image"},
        {"media_url": "file:///private/image.png"},
        {"group_avatar": True, "target_group_id": "unauthorized"},
    ],
)
async def test_invalid_media_sources_are_rejected_before_external_calls(source: JsonObject) -> None:
    bot = Bot()
    tools, context, _ = registry(bot)
    result = await tools.execute(context, "qq__read_media", source)
    assert result.status == "error" and not bot.calls


async def test_private_chat_cannot_read_a_group_avatar() -> None:
    bot = Bot()
    tools, context, _ = registry(
        bot, scope=ConversationScope(bot_id="1", kind="private", peer_id="20")
    )
    result = await tools.execute(context, "qq__read_media", {"group_avatar": True})
    assert result.status == "error" and not bot.calls


@pytest.mark.parametrize("redirect", ["https://evil.example/file", "https://private.qpic.cn/file"])
async def test_qq_image_redirect_rechecks_domain_and_public_address(redirect: str) -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": redirect})

    async def resolver(host: str) -> str:
        if host == "private.qpic.cn":
            raise ValueError("媒体 URL 不允许访问内网")
        return "8.8.8.8"

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        reader = MediaReader(client=client, resolver=resolver)
        segment = non_message_segment(GROUP, ReadMedia(user_id="20"))
        assert segment is not None
        result = await reader.read_many([segment], Bot())
    assert len(requests) == 1 and result["media"] == []
    assert isinstance(result["errors"], list) and len(result["errors"]) == 1


@pytest.mark.parametrize("body,budget", [(b"<html>error</html>", 1024), (PNG, 10)])
async def test_qq_images_reject_error_pages_and_excess_bytes(body: bytes, budget: int) -> None:
    def serve(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        reader = MediaReader(
            NapCatSettings(media_max_bytes=budget), client=client, resolver=public_ip
        )
        segment = non_message_segment(GROUP, ReadMedia(media_url="https://gchat.qpic.cn/image"))
        assert segment is not None
        result = await reader.read_many([segment], Bot())
    assert result["media"] == [] and result["errors"]


async def test_forward_inline_media_is_expanded_once_without_deduplicating_real_repeats() -> None:
    picture: JsonObject = {
        "type": "image",
        "data": {"file": "picture.png", "base64": base64.b64encode(PNG).decode()},
    }
    nodes: list[JsonValue] = [{"content": [picture, picture]}]

    async def remote(action: str, _: JsonObject) -> JsonValue:
        if action == "get_msg":
            raw = message()
            raw["message"] = [
                picture,
                {"type": "forward", "data": {"id": "outer", "content": nodes}},
            ]
            return raw
        assert action == "get_forward_msg"
        return {"messages": nodes}

    tools, context, _ = registry(Bot(remote))
    result = await tools.execute(context, "qq__read_media", {"message_id": "30"})
    assert result.status == "ok" and isinstance(result.data, dict)
    assert result.data["total"] == 3
    assert isinstance(result.data["media"], list) and len(result.data["media"]) == 3
    direct = await tools.execute(
        context, "qq__read_media", {"message_id": "30", "include_forwards": False}
    )
    assert isinstance(direct.data, dict) and direct.data["total"] == 1


@pytest.mark.parametrize("cancel_during_download", [False, True])
async def test_save_public_image_obeys_cancellation_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch, cancel_during_download: bool
) -> None:
    cancelled = False
    writes: list[tuple[str, bytes]] = []

    def serve(_: httpx.Request) -> httpx.Response:
        nonlocal cancelled
        cancelled = cancel_during_download
        return httpx.Response(200, content=PNG)

    async def write(path: str, content: bytes) -> None:
        writes.append((path, content))

    async def unused(_: ToolContext, __: JsonObject) -> ToolResult:
        raise AssertionError("仅用来声明已有工作区授权")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        reader = MediaReader(client=client, resolver=public_ip)
        monkeypatch.setattr("qq_agent_harness.napcat.tools.MediaReader", lambda _: reader)
        tools, context, settings = registry(Bot())
        settings.sandbox.enabled = True
        tools.add(
            ToolDefinition("sandbox__write_file", "写文件", "media.read", schema(Empty), unused)
        )
        context.write_workspace = write
        context.cancelled = lambda: cancelled
        result = await tools.execute(
            context,
            "qq__save_media_to_workspace",
            {"user_id": "20", "destination": "avatar.png"},
        )
    if cancel_during_download:
        assert result.status == "cancelled" and writes == []
    else:
        assert result.status == "ok" and writes == [("avatar.png", PNG)]
