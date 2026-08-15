"""旧生图插件的引用图片读取测试。"""

import unittest
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import cast
from unittest.mock import patch

import httpx

from app.database import GroupDataScope, StoredGroupMessage
from app.models import GroupMessage, Image, MessageSegment, Reply, Response, Sender, Text
from app.plugins.base import Context
from app.plugins.image_generate.image_generate import (
    ImageGenerateConfig,
    ImageGeneratePlugin,
)


class FakeBot:
    """仅提供图片刷新能力的测试 Bot。"""

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
            config = ImageGenerateConfig(
                group_ids=["40000"],
                model_name="image-model",
                model_vendors="image-vendor",
            )
            with patch(
                "app.plugins.image_generate.image_generate.load_plugin_config",
                return_value=config,
            ):
                plugin = ImageGeneratePlugin(context=cast(Context, context))
            try:
                collect_input_images = cast(
                    Callable[..., Awaitable[list[bytes]]],
                    getattr(plugin, "_collect_input_images"),
                )
                images = await collect_input_images(msg=build_command())
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
