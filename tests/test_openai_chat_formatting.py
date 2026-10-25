"""OpenAI 聊天消息格式测试。"""

import base64
import unittest
from typing import Final, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.services.llm.providers.openai import OpenAIService
from app.services.llm.schemas import ChatMessage

PNG_1X1_BASE64: Final[str] = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
PNG_1X1_BYTES: Final[bytes] = base64.b64decode(PNG_1X1_BASE64)


class InspectableOpenAIService(OpenAIService):
    """向测试公开聊天消息格式化结果。"""

    def format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> list[ChatCompletionMessageParam]:
        """调用受保护的协议格式化实现。"""
        return self._format_chat_messages(messages)


class OpenAIChatMessageFormattingTest(unittest.TestCase):
    """验证聊天消息使用上游兼容的 content 形态。"""

    def setUp(self) -> None:
        """创建只用于消息格式化的 OpenAI 服务。"""
        fake_client = object()
        self.service = InspectableOpenAIService(
            client=cast(AsyncOpenAI, fake_client)
        )

    def test_pure_text_messages_use_string_content(self) -> None:
        """纯文本多轮消息必须保留为字符串，避免上游忽略 assistant。"""
        formatted = self.service.format_chat_messages(
            [
                ChatMessage(role="system", text="系统提示"),
                ChatMessage(role="user", text="问题 A"),
                ChatMessage(role="assistant", text="回答 A"),
                ChatMessage(role="user", text="问题 B"),
            ]
        )

        self.assertEqual(
            formatted,
            [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "问题 A"},
                {"role": "assistant", "content": "回答 A"},
                {"role": "user", "content": "问题 B"},
            ],
        )

    def test_image_message_uses_multimodal_content_items(self) -> None:
        """只有带图片的消息才使用多模态 content 数组。"""
        formatted = self.service.format_chat_messages(
            [
                ChatMessage(
                    role="user",
                    text="看看这张图",
                    image=[PNG_1X1_BYTES],
                    image_detail="high",
                )
            ]
        )

        self.assertEqual(
            formatted,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看看这张图"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{PNG_1X1_BASE64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
        )

    def test_image_detail_can_be_omitted_from_provider_payload(self) -> None:
        """detail 未配置时不向兼容接口发送该字段。"""
        formatted = self.service.format_chat_messages(
            [ChatMessage(role="user", image=[PNG_1X1_BYTES])]
        )

        raw_content = formatted[0].get("content")
        self.assertIsInstance(raw_content, list)
        content = cast(list[dict[str, object]], raw_content)
        image_item = content[0]
        image_url = cast(dict[str, object], image_item["image_url"])
        self.assertNotIn("detail", image_url)
