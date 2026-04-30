"""AI 群聊输入构造测试。"""

import unittest
import asyncio
import tempfile
from pathlib import Path
from typing import cast

import httpx

from app.database import RedisDatabaseManager
from app.models import GroupMessage, Image, MessageSegment, Reply, Sender, Text
from app.plugins.ai_group_chat.config import AIGroupChatConfig, GroupChatConfig
from app.plugins.ai_group_chat.message_builder import GroupChatMessageBuilder


class EmptyDatabase:
    """测试用空消息数据库。"""

    async def search_messages(
        self,
        *,
        self_id: str,
        message_id: str | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: str | None = None,
        user_id: str | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> None:
        """不返回引用消息。"""
        _ = (
            self_id,
            message_id,
            root,
            limit_tuple,
            group_id,
            user_id,
            max_time,
            min_time,
        )
        return None


class ReplyDatabase:
    """测试用引用消息数据库。"""

    def __init__(self, reply_message: GroupMessage) -> None:
        """保存固定引用消息。"""
        self.reply_message: GroupMessage = reply_message

    async def search_messages(
        self,
        *,
        self_id: str,
        message_id: str | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: str | None = None,
        user_id: str | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> GroupMessage:
        """返回固定引用消息。"""
        _ = (
            self_id,
            message_id,
            root,
            limit_tuple,
            group_id,
            user_id,
            max_time,
            min_time,
        )
        return self.reply_message


def build_config() -> AIGroupChatConfig:
    """构造测试用插件配置。"""
    return AIGroupChatConfig(
        model_name="gpt-5.5",
        model_vendors="CLIProxyAPI",
        supports_multimodal=False,
        multimodal_fallback_model_name="gpt-5.5-vision",
        multimodal_fallback_model_vendors="CLIProxyAPI",
        group_config=[
            GroupChatConfig(
                group_id="40000",
                system_prompt_path="unused",
                knowledge_base_path="unused",
                max_context_tokens=1000000,
            )
        ],
    )


def build_message(
    *, message: list[MessageSegment] | None = None, raw_message: str = "你好呀"
) -> GroupMessage:
    """构造测试用群消息。"""
    if message is None:
        message = [Text.new("你好呀")]
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id="30000",
        group_id="40000",
        group_name="测试群",
        message=message,
        raw_message=raw_message,
        sender=Sender(user_id="20000", nickname="夜袭", role="member"),
    )


class GroupChatMessageBuilderTest(unittest.TestCase):
    """验证群消息到 Markdown 输入的转换。"""

    def test_current_message_is_markdown_without_message_id(self) -> None:
        """当前群消息会转成低噪音 Markdown，不暴露消息 ID。"""
        builder = GroupChatMessageBuilder(
            config=build_config(),
            database=cast(RedisDatabaseManager, EmptyDatabase()),
            http_client=cast(httpx.AsyncClient, object()),
        )

        chat_message = asyncio.run(
            builder.build_user_message(msg=build_message())
        )

        self.assertIsNotNone(chat_message.text)
        text = chat_message.text or ""
        self.assertIn("## 当前消息", text)
        self.assertIn("- 群: 测试群 (40000)", text)
        self.assertIn("- 群员: 夜袭 (20000, 群员)", text)
        self.assertIn("你好呀", text)
        self.assertNotIn("message_id", text)

    def test_user_message_never_contains_deepseek_depth_zero_prompt(self) -> None:
        """真实用户消息只描述当前群消息，不携带 DSV4 Depth 0 提示词。"""
        builder = GroupChatMessageBuilder(
            config=build_config(),
            database=cast(RedisDatabaseManager, EmptyDatabase()),
            http_client=cast(httpx.AsyncClient, object()),
        )

        chat_message = asyncio.run(builder.build_user_message(msg=build_message()))

        text = chat_message.text or ""
        self.assertNotIn("<其他需求>", text)
        self.assertNotIn("<角色沉浸式扮演需求>", text)
        self.assertNotIn("【角色沉浸要求】", text)

    def test_image_message_without_collection_marks_image_unattached(self) -> None:
        """未收集图片时，输入文本明确说明图片未随请求附带。"""
        builder = GroupChatMessageBuilder(
            config=build_config(),
            database=cast(RedisDatabaseManager, EmptyDatabase()),
            http_client=cast(httpx.AsyncClient, object()),
        )
        msg = build_message(
            message=[Text.new("看看图"), Image.new("image.png")],
            raw_message="看看图[图片]",
        )

        built_messages = asyncio.run(
            builder.build_turn_messages(msg=msg, collect_images=False)
        )
        chat_message = built_messages.turn_messages[0]

        self.assertTrue(built_messages.contains_image)
        self.assertEqual(built_messages.loaded_image_count, 0)
        self.assertIsNone(chat_message.image)
        self.assertIn("当前请求未附带图片内容", chat_message.text or "")

    def test_current_and_reply_images_are_collected_when_enabled(self) -> None:
        """开启图片收集时，当前消息和引用消息图片都会进入本轮输入。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_path = root / "current.bin"
            reply_path = root / "reply.bin"
            current_path.write_bytes(b"current-image")
            reply_path.write_bytes(b"reply-image")
            reply_message = build_message(
                message=[Text.new("引用图"), Image.new("reply.png", path=str(reply_path))],
                raw_message="引用图[图片]",
            )
            builder = GroupChatMessageBuilder(
                config=build_config(),
                database=cast(RedisDatabaseManager, ReplyDatabase(reply_message)),
                http_client=cast(httpx.AsyncClient, object()),
            )
            msg = build_message(
                message=[
                    Text.new("看看两张图"),
                    Image.new("current.png", path=str(current_path)),
                    Reply.new("reply-message-id"),
                ],
                raw_message="看看两张图[图片]",
            )

            built_messages = asyncio.run(
                builder.build_turn_messages(msg=msg, collect_images=True)
            )
            chat_message = built_messages.turn_messages[0]

            self.assertTrue(built_messages.contains_image)
            self.assertEqual(built_messages.loaded_image_count, 2)
            self.assertEqual(chat_message.image, [b"current-image", b"reply-image"])
            self.assertIn("图片已随本条输入提供", chat_message.text or "")
