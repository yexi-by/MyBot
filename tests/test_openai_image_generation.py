"""OpenAI 协议请求参数测试。"""

import base64
import unittest
from typing import Final, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.image import Image as OpenAIImage
from openai.types.images_response import ImagesResponse

from app.services.llm.providers.openai import OpenAIService
from app.services.llm.schemas import ChatMessage

PNG_1X1_BASE64: Final[str] = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
PNG_1X1_BYTES: Final[bytes] = base64.b64decode(PNG_1X1_BASE64)
RESULT_IMAGE_BASE64: Final[str] = "ZmFrZV9pbWFnZQ=="


class FakeImagesResource:
    """记录图片接口调用，并用严格签名阻止额外参数进入请求。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.generate_calls: list[tuple[str, str]] = []
        self.edit_calls: list[tuple[list[tuple[str, bytes, str]], str, str]] = []

    async def generate(self, *, model: str, prompt: str) -> ImagesResponse:
        """模拟文生图接口，只接受必需参数。"""
        self.generate_calls.append((model, prompt))
        return self._build_response()

    async def edit(
        self, *, image: list[tuple[str, bytes, str]], model: str, prompt: str
    ) -> ImagesResponse:
        """模拟图生图接口，只接受必需参数。"""
        self.edit_calls.append((image, model, prompt))
        return self._build_response()

    def _build_response(self) -> ImagesResponse:
        """构造带 base64 图片结果的响应。"""
        return ImagesResponse(
            created=0,
            data=[OpenAIImage(b64_json=RESULT_IMAGE_BASE64)],
        )


class FakeOpenAIClient:
    """提供 OpenAIService 所需的最小 images 入口。"""

    def __init__(self) -> None:
        """初始化假的图片资源。"""
        self.images = FakeImagesResource()


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
        fake_client = FakeOpenAIClient()
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
                                "detail": "auto",
                            },
                        },
                    ],
                }
            ],
        )


class OpenAIImageGenerationParameterTest(unittest.IsolatedAsyncioTestCase):
    """验证生图请求不会携带模型不支持的可选参数。"""

    async def test_text_to_image_only_sends_required_fields(self) -> None:
        """文生图只传 model 和 prompt。"""
        fake_client = FakeOpenAIClient()
        service = OpenAIService(client=cast(AsyncOpenAI, fake_client))

        result = await service.get_image(
            message=ChatMessage(role="user", text="画一只猫"),
            model="gpt-image-2",
        )

        self.assertEqual(result, RESULT_IMAGE_BASE64)
        self.assertEqual(fake_client.images.generate_calls, [("gpt-image-2", "画一只猫")])
        self.assertEqual(fake_client.images.edit_calls, [])

    async def test_image_to_image_only_sends_required_fields(self) -> None:
        """图生图只传 image、model 和 prompt。"""
        fake_client = FakeOpenAIClient()
        service = OpenAIService(client=cast(AsyncOpenAI, fake_client))

        result = await service.get_image(
            message=ChatMessage(role="user", text="改成水彩风", image=[PNG_1X1_BYTES]),
            model="gpt-image-2",
        )

        self.assertEqual(result, RESULT_IMAGE_BASE64)
        self.assertEqual(fake_client.images.generate_calls, [])
        self.assertEqual(len(fake_client.images.edit_calls), 1)
        image_files, model, prompt = fake_client.images.edit_calls[0]
        self.assertEqual(model, "gpt-image-2")
        self.assertEqual(prompt, "改成水彩风")
        self.assertEqual(image_files, [("input_1.png", PNG_1X1_BYTES, "image/png")])
