from typing import cast

from google import genai
from google.genai import types

from ..base import LLMProvider
from ..schemas import ChatMessage


class GeminiService(LLMProvider):
    def __init__(self, client: genai.Client) -> None:
        self.client = client

    def _format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[list[types.Content], str]:
        chat_messages = []
        system_prompt = ""
        role_map = {
            "user": "user",
            "assistant": "model",
        }
        for msg in messages:
            if msg.role == "system":
                system_prompt = cast(
                    str, msg.text
                )  # 由于系统提示词不会是None，直接断言,
                continue
            role = role_map[msg.role]
            parts = []
            if msg.text:
                parts.append(types.Part.from_text(text=msg.text))
            if msg.image:
                parts.append(
                    types.Part.from_bytes(data=msg.image, mime_type="image/jpeg")
                )
            content = types.Content(role=role, parts=parts)
            chat_messages.append(content)
        return chat_messages, system_prompt

    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs,
    ) -> str:
        chat_messages, system_prompt = self._format_chat_messages(messages=messages)
        response = await self.client.aio.models.generate_content(
            model=model,
            contents=chat_messages,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=1,
            ),
        )
        content = response.text
        return content  # type: ignore
