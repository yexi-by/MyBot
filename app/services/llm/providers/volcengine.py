import base64
from typing import cast

from volcenginesdkarkruntime import AsyncArk
from volcenginesdkarkruntime.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
)

from app.utils.utils import detect_image_mime_type

from ..base import LLMProvider
from ..schemas import ChatMessage


class VolcengineService(LLMProvider):
    def __init__(self, client: AsyncArk) -> None:
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
                    mime_type = detect_image_mime_type(image_bytes)
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

    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs,
    ) -> str:
        chat_messages = self._format_chat_messages(messages)
        response = cast(
            ChatCompletion,
            await self.client.chat.completions.create(
                messages=chat_messages, model=model, stream=False
            ),
        )
        content = response.choices[0].message.content
        return content  # type:ignore

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        if not message.text:
            raise ValueError("提示词为空请重新输入")

        prompt = message.text
        images = None
        if message.image:
            images = []
            for img_bytes in message.image:
                mime_type = detect_image_mime_type(img_bytes)
                base64_str = base64.b64encode(img_bytes).decode("utf-8")
                images.append(f"data:{mime_type};base64,{base64_str}")

        # 调用图片生成接口
        response = await self.client.images.generate(
            model=model,
            prompt=prompt,
            image=images,  # 传入 list[str] 或 None，支持多图融合
            response_format="b64_json",  # 指定返回 base64 格式
            size="2K",
            watermark=False,
        )

        # 从响应中提取 base64 编码的图片数据
        b64_string = response.data[0].b64_json

        return b64_string
