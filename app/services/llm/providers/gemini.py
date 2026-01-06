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
                    parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
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
        prompt: str,
        model: str,
        image_base64_list: list[str] | None = None,
    ) -> str:
        """
        生成图片

        Args:
            prompt: 文本提示词
            model: 模型名称 (如 gemini-2.5-flash-image 或 gemini-3-pro-image-preview)
            image_base64_list: 可选的图片列表(base64编码的字符串)，用于图文生图

        Returns:
            生成的图片base64编码字符串
        """
        # 构建contents列表
        contents: list[types.Part | str] = [prompt]

        # 如果提供了图片，添加到contents
        if image_base64_list:
            for base64_str in image_base64_list:
                # 将base64字符串解码为bytes
                image_data = base64.b64decode(base64_str)
                mime_type = detect_image_mime_type(image_data)
                contents.append(
                    types.Part.from_bytes(data=image_data, mime_type=mime_type)
                )

        # 调用API生成图片
        response = await self.client.aio.models.generate_content(
            model=model,
            contents=contents,  # type: ignore
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],  # 只返回图片，不返回文本
            ),
        )

        # 提取生成的图片数据
        if response.parts:
            for part in response.parts:
                if part.inline_data is not None and part.inline_data.data is not None:
                    base64_result = base64.b64encode(part.inline_data.data).decode("utf-8")
                    return base64_result
        raise ValueError("未能从响应中获取图片数据")
