"""旧生图插件的引用图片读取测试。"""

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import cast

import httpx

from app.config import ImageGenerateConfig, ModelRef
from app.database import GroupDataScope, StoredGroupMessage
from app.models import GroupMessage, Image, MessageSegment, Reply, Response, Sender, Text
from app.plugins.base import Context
from app.plugins.image_generate.image_generate import ImageGeneratePlugin
from app.services.llm.errors import LLMRequestError
from tests.config_helpers import (
    FakeConfigManager,
    build_plugin_snapshot,
    plugin_config_view,
)


class FakeBot:
    """仅提供图片刷新能力的测试 Bot。"""

    def __init__(self) -> None:
        self.sent_texts: list[str] = []

    async def send_msg(self, *, group_id: str, at: str, text: str) -> Response:
        _ = (group_id, at)
        self.sent_texts.append(text)
        return Response(status="ok", retcode=0)

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """用例应直接读取 URL，不应刷新图片。"""
        _ = (file_id, file)
        raise AssertionError("引用图片 URL 可用时不应请求 NapCat 刷新")


class FakeGroupMessages:
    """返回一条固定的未撤回引用消息。"""

    def __init__(self, message: StoredGroupMessage) -> None:
        self.message = message
        self.calls: list[tuple[GroupDataScope, str]] = []

    async def get_active(
        self, *, scope: GroupDataScope, message_id: str
    ) -> StoredGroupMessage | None:
        """记录受群作用域限制的引用查询。"""
        self.calls.append((scope, message_id))
        return self.message


class FakeContext:
    """生图插件本用例所需的最小上下文。"""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        group_messages: FakeGroupMessages,
    ) -> None:
        self.bot = FakeBot()
        self.direct_httpx = http_client
        self.group_messages = group_messages
        self.llm = WaitingImageLLM()


class WaitingImageLLM:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def get_image(self, **_kwargs: object) -> str:
        self.entered.set()
        await self.release.wait()
        raise LLMRequestError("模型请求超时")


def build_stored_reply() -> StoredGroupMessage:
    """构造带可用 URL 图片的历史消息。"""
    return StoredGroupMessage(
        row_id=1,
        scope=GroupDataScope(bot_id="10000", group_id="40000"),
        message_id="quoted-message",
        group_name="测试群",
        sender_id="20000",
        sender_name="测试用户",
        sender_role="member",
        occurred_at=datetime.now(timezone.utc),
        direction="incoming",
        segments=(
            Image.new(
                "quoted.png",
                url="https://media.example/quoted.png",
            ),
        ),
        images=(),
    )


def build_command() -> GroupMessage:
    """构造引用历史图片的生图指令。"""
    message: list[MessageSegment] = [Reply.new("quoted-message"), Text.new("/生图 改成夜景")]
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20001",
        message_id="command-message",
        group_id="40000",
        group_name="测试群",
        message=message,
        raw_message="/生图 改成夜景",
        sender=Sender(user_id="20001", nickname="发起者", role="member"),
    )


class ImageGeneratePluginTest(unittest.IsolatedAsyncioTestCase):
    """验证引用消息 DTO 和共享图片读取服务的组合。"""

    async def test_busy_image_queue_does_not_block_unrelated_group(self) -> None:
        config = ImageGenerateConfig(
            groups=("40000",), model=ModelRef(provider="main", name="image"),
            fetch_concurrency=1, download_timeout_seconds=1, max_input_image_bytes=0,
            command="/生图", help_command="/help生图",
        )
        async with httpx.AsyncClient() as client:
            context = FakeContext(http_client=client, group_messages=FakeGroupMessages(build_stored_reply()))
            llm = context.llm
            plugin = ImageGeneratePlugin(
                context=cast(Context, context),
                plugin_config=plugin_config_view(FakeConfigManager(build_plugin_snapshot(image_generate=config)), plugin_id="image_generate"),
                consumers_count=1, stop_timeout_seconds=1,
            )
            event = build_command().model_copy(update={"message": [Text.new("/生图 test")]})
            pending = asyncio.create_task(plugin.add_to_queue(event))
            try:
                await asyncio.wait_for(llm.entered.wait(), 1)
                unrelated = event.model_copy(update={"group_id": "other", "message": [Text.new("普通消息")]})
                self.assertFalse(await asyncio.wait_for(plugin.add_to_queue(unrelated), 1))
                self.assertFalse(pending.done())
                llm.release.set()
                self.assertTrue(await pending)
                self.assertIn("生图失败", context.bot.sent_texts[-1])
            finally:
                await plugin.stop_consumers()
                _ = await asyncio.gather(pending, return_exceptions=True)

    async def test_replied_image_uses_group_reader_and_shared_image_reader(self) -> None:
        """引用图片会按当前群查询，并直接使用消息中的 URL。"""
        requested_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            return httpx.Response(200, content=b"quoted-image")

        group_messages = FakeGroupMessages(build_stored_reply())
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            context = FakeContext(
                http_client=http_client,
                group_messages=group_messages,
            )
            config = ImageGenerateConfig.model_validate(
                {
                    "groups": ["40000"],
                    "model": {
                        "provider": "image-vendor",
                        "name": "image-model",
                    },
                    "fetch_concurrency": 16,
                    "download_timeout_seconds": 20,
                    "max_input_image_bytes": 0,
                    "command": "/生图",
                    "help_command": "/help生图",
                }
            )
            manager = FakeConfigManager(
                build_plugin_snapshot(image_generate=config)
            )
            plugin = ImageGeneratePlugin(
                context=cast(Context, context),
                plugin_config=plugin_config_view(
                    manager,
                    plugin_id="image_generate",
                ),
                consumers_count=1,
                stop_timeout_seconds=1,
            )
            try:
                runtime = plugin._current_runtime()  # pyright: ignore[reportPrivateUsage]
                if runtime is None:
                    raise AssertionError("生图测试配置应启用插件")
                collect_input_images = cast(
                    Callable[..., Awaitable[list[bytes]]],
                    getattr(plugin, "_collect_input_images"),
                )
                images = await collect_input_images(
                    msg=build_command(),
                    image_reader=runtime.image_reader,
                )
            finally:
                await plugin.stop_consumers()

        self.assertEqual(images, [b"quoted-image"])
        self.assertEqual(requested_urls, ["https://media.example/quoted.png"])
        self.assertEqual(
            group_messages.calls,
            [(GroupDataScope(bot_id="10000", group_id="40000"), "quoted-message")],
        )


if __name__ == "__main__":
    unittest.main()
