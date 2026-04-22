import base64
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.images_response import ImagesResponse

from app.utils.utils import detect_mime_type

from ..base import LLMProvider
from ..schemas import ChatMessage


class OpenAIService(LLMProvider):
    """OpenAI 兼容服务实现。"""

    def __init__(self, client: AsyncOpenAI) -> None:
        self.client = client

    def _format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> list[ChatCompletionMessageParam]:
        chat_messages = []
        for msg in messages:
            msg_dict = {}
            content_lst = []
            msg_dict["role"] = msg.role
            if msg.text:
                content_lst.append({"type": "text", "text": msg.text})
            if msg.image:
                for image_bytes in msg.image:
                    mime_type = detect_mime_type(image_bytes)
                    base64_image = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"
                    content_lst.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": base64_image, "detail": "auto"},
                        }
                    )
            msg_dict["content"] = content_lst
            chat_messages.append(msg_dict)
        return chat_messages

    def _build_image_files(
        self,
        images: list[bytes],
    ) -> list[tuple[str, bytes, str]]:
        """将图片字节转换为 OpenAI 兼容编辑接口所需的文件列表。"""
        files: list[tuple[str, bytes, str]] = []
        for index, image_bytes in enumerate(images, start=1):
            mime_type = detect_mime_type(image_bytes)
            extension = mime_type.split("/")[-1]
            file_name = f"input_{index}.{extension}"
            files.append((file_name, image_bytes, mime_type))
        return files

    def _extract_base64_image(self, response: ImagesResponse) -> str:
        """从图片接口响应中提取首张 base64 图片。"""
        if not response.data:
            raise ValueError("图片接口返回了空结果")
        for image_data in response.data:
            if image_data.b64_json:
                return image_data.b64_json
        raise ValueError("未能从响应中获取图片数据")

    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs,
    ) -> str:
        chat_messages = self._format_chat_messages(messages)
        response = await self.client.chat.completions.create(
            model=model,
            messages=chat_messages,
            **kwargs
        )
        content = response.choices[0].message.content
        return content  # type:ignore

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
        **kwargs,
    ) -> str:
        """使用标准 OpenAI 兼容图片接口生成图片。"""
        if not message.text:
            raise ValueError("提示词为空请重新输入")
        if "model" in kwargs or "prompt" in kwargs or "image" in kwargs:
            raise ValueError("请使用 get_image 的 message 和 model 参数传入核心字段")
        if kwargs.get("response_format") not in (None, "b64_json"):
            raise ValueError("当前 get_image 仅支持 b64_json 返回格式")
        if kwargs.get("stream") not in (None, False):
            raise ValueError("当前 get_image 不支持流式图片响应")

        request_kwargs = dict(kwargs)
        request_kwargs["model"] = model
        request_kwargs["prompt"] = message.text
        request_kwargs["response_format"] = "b64_json"
        request_kwargs["stream"] = False

        response: ImagesResponse
        if message.image:
            image_files = self._build_image_files(message.image)
            response = cast(
                ImagesResponse,
                await self.client.images.edit(
                    image=image_files,
                    **request_kwargs,
                ),
            )
        else:
            response = cast(
                ImagesResponse,
                await self.client.images.generate(
                    **request_kwargs,
                ),
            )
        return self._extract_base64_image(response)
