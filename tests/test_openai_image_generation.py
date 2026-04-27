"""OpenAI 兼容图片接口请求参数测试。"""

import base64
import unittest
from typing import Final, cast

from openai import AsyncOpenAI
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
