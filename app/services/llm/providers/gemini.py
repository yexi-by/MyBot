import base64
from typing import cast

from google import genai
from google.genai import types

from app.utils.utils import detect_image_mime_type

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
                for image_bytes in msg.image:
                    mime_type = detect_image_mime_type(image_bytes)
                    parts.append(
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
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
            contents=chat_messages,  # type: ignore
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=1,
            ),
        )
        content = response.text
        return content  # type: ignore

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        if not message.text:
            raise ValueError("提示词为空请重新输入")
        contents: list[str | types.Part] = [message.text]
        if message.image:
            for image_bytes in message.image:
                mime_type = detect_image_mime_type(image_bytes)
                contents.append(
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                )

        # 流式请求
        async for response in await self.client.aio.models.generate_content_stream(
            model=model,
            contents=contents,  # type: ignore
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        ):
            if not response.candidates:
                continue

            content = response.candidates[0].content
            if not content or not content.parts:
                continue

            for part in content.parts:
                if part.inline_data and part.inline_data.data:
                    return base64.b64encode(part.inline_data.data).decode("utf-8")

        raise ValueError("未能从响应中获取图片数据")
