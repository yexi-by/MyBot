import base64

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.utils.utils import detect_mime_type

from ..base import LLMProvider
from ..schemas import ChatMessage


class OpenAIService(LLMProvider):
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
        )
        content = response.choices[0].message.content
        return content  # type:ignore

    async def get_image(self, message: ChatMessage, model: str) -> str:
        chat_messages = self._format_chat_messages(messages=[message])
        response = await self.client.chat.completions.create(
            model=model, messages=chat_messages
        )
        image_data = response.choices[0].message.content
        if not image_data:
            raise ValueError("未能从响应中获取图片数据")
        return image_data
