"""视频读取、消息输入及协议交付的功能测试。"""

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast

import httpx
from openai import AsyncOpenAI

from app.database import GroupMessageReader
from app.models import Reply, Response, Video
from app.plugins.ai_group_chat.message_builder import GroupChatMessageBuilder
from app.services.llm.schemas import ChatMessage
from app.services.napcat.video_reader import NapCatVideoReader, NapCatVideoResource
from tests.test_ai_group_chat_message_builder import (
    EmptyDatabase, MissingImageBot, ReplyDatabase, build_config, build_message,
)
from tests.test_openai_image_generation import InspectableOpenAIService


VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


class VideoBot(MissingImageBot):
    def __init__(self) -> None:
        self.refreshes = 0

    async def get_file(self, file_id: str | None = None, file: str | None = None) -> Response:
        self.refreshes += 1
        return Response(status="ok", retcode=0, data={"url": "https://media.test/fresh"})


class VideoInputTest(unittest.IsolatedAsyncioTestCase):
    async def test_path_then_url_then_refresh_and_partial_failure(self) -> None:
        """归档路径优先，失效 URL 才刷新，一个无效视频不会丢掉整批。"""
        calls: list[str] = []

        def download(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/expired":
                return httpx.Response(403)
            return httpx.Response(200, content=VIDEO_BYTES)

        bot = VideoBot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archived.mp4"
            path.write_bytes(VIDEO_BYTES)
            async with httpx.AsyncClient(transport=httpx.MockTransport(download)) as client:
                reader = NapCatVideoReader(bot=bot, http_client=client, fetch_concurrency=2,
                                           download_timeout_seconds=1, max_video_bytes=1024)
                results = await reader.read_many(resources=[
                    NapCatVideoResource("本地", "one.mp4", path=str(path), url="https://media.test/unused"),
                    NapCatVideoResource("直连", "two.mp4", url="https://media.test/direct"),
                    NapCatVideoResource("刷新", "three.mp4", url="https://media.test/expired"),
                ])
        self.assertEqual([item.source for item in results], ["direct_path", "direct_url", "napcat_refresh"])
        self.assertEqual([item.video_bytes for item in results], [VIDEO_BYTES] * 3)
        self.assertEqual(bot.refreshes, 1)
        self.assertNotIn("/unused", calls)

    async def test_timeout_size_limit_and_concurrency_preserve_order(self) -> None:
        active = peak = 0

        async def download(request: httpx.Request) -> httpx.Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                if request.url.path == "/slow":
                    await asyncio.Event().wait()
                await asyncio.sleep(0.005)
                if request.url.path == "/large":
                    return httpx.Response(200, headers={"content-length": "2048"})
                return httpx.Response(200, content=VIDEO_BYTES)
            finally:
                active -= 1

        async with httpx.AsyncClient(transport=httpx.MockTransport(download)) as client:
            reader = NapCatVideoReader(bot=MissingImageBot(), http_client=client, fetch_concurrency=2,
                                       download_timeout_seconds=0.05, max_video_bytes=1024)
            results = await reader.read_many(resources=[
                NapCatVideoResource(name, "", url=f"https://media.test/{name}")
                for name in ("slow", "good", "large", "another")
            ])
        self.assertLessEqual(peak, 2)
        self.assertIsNone(results[0].video_bytes)
        self.assertIn("TimeoutError", results[0].error or "")
        self.assertEqual(results[1].video_bytes, VIDEO_BYTES)
        self.assertEqual(results[2].error_type, "VideoReadTooLargeError")
        self.assertEqual(results[3].video_bytes, VIDEO_BYTES)

    async def test_switch_and_quoted_archive_with_expired_url(self) -> None:
        """关闭开关不访问视频；开启后读取当前和引用视频，并遵守每轮数量。"""
        calls: list[str] = []

        def download(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, content=VIDEO_BYTES)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quoted.mp4"
            path.write_bytes(VIDEO_BYTES)
            database = ReplyDatabase(build_message(message=[Video.new(
                "quoted.mp4", path=str(path), url="https://media.test/expired",
            )]))
            msg = build_message(message=[
                Reply.new("quoted-id"), Video.new("current.mp4", url="https://media.test/current"),
            ])
            async with httpx.AsyncClient(transport=httpx.MockTransport(download)) as client:
                def builder(enabled: bool, limit: int) -> GroupChatMessageBuilder:
                    return GroupChatMessageBuilder(
                        config=build_config(videos={"enabled": enabled, "max_per_turn": limit}),
                        group_messages=cast(GroupMessageReader, database), bot=VideoBot(), http_client=client,
                    )

                disabled = await builder(False, 2).build_turn_messages(msg=msg)
                self.assertFalse(any(item.video for item in disabled.turn_messages))
                self.assertEqual(calls, [])
                enabled = await builder(True, 2).build_turn_messages(msg=msg)
                self.assertEqual([item.video for item in enabled.turn_messages[1:]], [[VIDEO_BYTES], [VIDEO_BYTES]])
                self.assertIn("引用消息", enabled.turn_messages[2].text or "")
                self.assertEqual(calls, ["/current"])
                limited = await builder(True, 1).build_turn_messages(msg=msg)
                self.assertEqual(json.loads(limited.turn_messages[-1].text or "{}")["truncated_count"], 1)

    async def test_bad_video_is_reported_to_model(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as client:
            builder = GroupChatMessageBuilder(
                config=build_config(videos={"enabled": True}), group_messages=cast(GroupMessageReader, EmptyDatabase()),
                bot=MissingImageBot(), http_client=client,
            )
            turn = await builder.build_turn_messages(msg=build_message(message=[Video.new("missing.mp4")]))
        error = json.loads(turn.turn_messages[-1].text or "{}")
        self.assertTrue(error["is_error"])
        self.assertEqual(error["loaded_count"], 0)
        self.assertEqual(len(error["errors"]), 1)

    async def test_native_protocol_and_serialization(self) -> None:
        client = AsyncOpenAI(api_key="test", base_url="https://model.test/v1")
        try:
            service = InspectableOpenAIService(client=client)
            message = ChatMessage(role="user", text="看视频", video=[VIDEO_BYTES])
            payload = service.format_chat_messages([message])
            self.assertEqual(payload, [{"role": "user", "content": [
                {"type": "text", "text": "看视频"},
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(VIDEO_BYTES).decode()}},
            ]}])
            self.assertNotIn(base64.b64encode(VIDEO_BYTES).decode(), message.model_dump_json())
            self.assertIsNone(message.without_videos().video)
        finally:
            await client.close()
