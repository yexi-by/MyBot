import base64
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
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
                base64_image = f"data:image/jpeg;base64,{base64.b64encode(msg.image).decode('utf-8')}"
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
