"""AI 群聊输入构造测试。"""

import unittest
import asyncio
from typing import cast

import httpx

from app.database import RedisDatabaseManager
from app.models import GroupMessage, Sender, Text
from app.plugins.ai_group_chat.config import AIGroupChatConfig, GroupChatConfig
from app.plugins.ai_group_chat.constants import DEEPSEEK_V4_ROLEPLAY_INSTRUCT
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


def build_config() -> AIGroupChatConfig:
    """构造测试用插件配置。"""
    return AIGroupChatConfig(
        model_name="gpt-5.5",
        model_vendors="CLIProxyAPI",
        supports_multimodal=False,
        group_config=[
            GroupChatConfig(
                group_id="40000",
                system_prompt_path="unused",
                knowledge_base_path="unused",
                max_context_tokens=1000000,
            )
        ],
    )


def build_message() -> GroupMessage:
    """构造测试用群消息。"""
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
        message=[Text.new("你好呀")],
        raw_message="你好呀",
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
            builder.build_user_message(
                msg=build_message(),
                append_deepseek_v4_roleplay_instruct=False,
            )
        )

        self.assertIsNotNone(chat_message.text)
        text = chat_message.text or ""
        self.assertIn("## 当前消息", text)
        self.assertIn("- 群: 测试群 (40000)", text)
        self.assertIn("- 群员: 夜袭 (20000, 群员)", text)
        self.assertIn("你好呀", text)
        self.assertNotIn("message_id", text)

    def test_deepseek_roleplay_marker_appends_to_user_message_tail(self) -> None:
        """DeepSeek V4 Marker 会追加到首轮用户提示词末尾。"""
        builder = GroupChatMessageBuilder(
            config=build_config(),
            database=cast(RedisDatabaseManager, EmptyDatabase()),
            http_client=cast(httpx.AsyncClient, object()),
        )

        chat_message = asyncio.run(
            builder.build_user_message(
                msg=build_message(),
                append_deepseek_v4_roleplay_instruct=True,
            )
        )

        text = chat_message.text or ""
        self.assertTrue(text.endswith(DEEPSEEK_V4_ROLEPLAY_INSTRUCT))
