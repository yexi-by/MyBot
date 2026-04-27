"""OpenAI 兼容 LLM 服务实现。"""

import base64
import json
from typing import Final, cast, override

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
    ChatCompletionToolParam,
)
from openai.types.images_response import ImagesResponse

from app.models import JsonObject
from app.utils.file_type import detect_mime_type

from ..base import LLMProvider
from ..schemas import (
    ChatMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolChoice,
    LLMToolDefinition,
)

REASONING_FIELD_NAMES: Final[tuple[str, ...]] = (
    "reasoning_content",
    "reasoning",
    "reasoning_summary",
    "reasoning_text",
)
REASONING_TEXT_KEYS: Final[tuple[str, ...]] = (
    "reasoning_content",
    "content",
    "text",
    "summary",
    "output_text",
)
class OpenAIService(LLMProvider):
    """OpenAI 兼容服务实现。"""

    def __init__(self, client: AsyncOpenAI) -> None:
        """保存 OpenAI 异步客户端。"""
        self.client: AsyncOpenAI = client

    def _format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> list[ChatCompletionMessageParam]:
        """转换为 OpenAI Chat Completions 消息格式。"""
        chat_messages: list[ChatCompletionMessageParam] = []
        for msg in messages:
            if msg.role == "tool":
                if msg.tool_call_id is None or msg.text is None:
                    raise ValueError("tool 消息缺少 tool_call_id 或 text")
                raw_tool_message = {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.text,
                }
                chat_messages.append(
                    cast(ChatCompletionMessageParam, cast(object, raw_tool_message))
                )
                continue

            content_items: list[dict[str, object]] = []
            if msg.text:
                content_items.append({"type": "text", "text": msg.text})
            if msg.image:
                for image_bytes in msg.image:
                    mime_type = detect_mime_type(image_bytes)
                    image_data = base64.b64encode(image_bytes).decode("utf-8")
                    base64_image = f"data:{mime_type};base64,{image_data}"
                    content_items.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": base64_image, "detail": "auto"},
                        }
                    )
            raw_message: dict[str, object] = {
                "role": msg.role,
                "content": content_items if content_items else msg.text,
            }
            if msg.tool_calls:
                raw_message["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(
                                tool_call.arguments, ensure_ascii=False
                            ),
                        },
                    }
                    for tool_call in msg.tool_calls
                ]
            if msg.role == "assistant" and msg.reasoning_content is not None:
                raw_message["reasoning_content"] = msg.reasoning_content
            chat_messages.append(
                cast(ChatCompletionMessageParam, cast(object, raw_message))
            )
        return chat_messages

    def _format_tools(
        self, tools: list[LLMToolDefinition]
    ) -> list[ChatCompletionToolParam]:
        """转换为 OpenAI Chat Completions tools 参数。"""
        formatted_tools: list[ChatCompletionToolParam] = []
        for tool in tools:
            raw_tool = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "strict": tool.strict,
                },
            }
            formatted_tools.append(cast(ChatCompletionToolParam, cast(object, raw_tool)))
        return formatted_tools

    def _build_image_files(
        self,
        images: list[bytes],
    ) -> list[tuple[str, bytes, str]]:
        """将图片字节转换为 OpenAI 编辑接口所需的文件列表。"""
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

    def _extract_reasoning_content(
        self, message: ChatCompletionMessage
    ) -> str | None:
        """提取 OpenAI 兼容服务显式返回的可展示思考内容。"""
        candidates: list[object] = []
        raw_extra = cast(object, message.model_extra)
        if isinstance(raw_extra, dict):
            extra_items = cast(dict[str, object], raw_extra)
            candidates.extend(
                extra_items.get(field_name)
                for field_name in REASONING_FIELD_NAMES
            )
        raw_dump = cast(
            object, message.model_dump(mode="json", exclude_none=True)
        )
        if isinstance(raw_dump, dict):
            dumped_items = cast(dict[str, object], raw_dump)
            candidates.extend(
                dumped_items.get(field_name)
                for field_name in REASONING_FIELD_NAMES
            )
        reasoning_parts: list[str] = []
        for candidate in candidates:
            reasoning_text = self._extract_reasoning_text(candidate)
            if reasoning_text is not None:
                reasoning_parts.append(reasoning_text)
        if not reasoning_parts:
            return None
        return "\n".join(dict.fromkeys(reasoning_parts))

    def _extract_reasoning_text(self, value: object) -> str | None:
        """从常见 reasoning 字段形态中提取文本。"""
        if isinstance(value, str):
            stripped_value = value.strip()
            if stripped_value:
                return stripped_value
            return None
        if isinstance(value, list):
            text_parts: list[str] = []
            # 第三方兼容接口的 reasoning 明细数组没有稳定类型，入口处收窄为 object 列表逐项解析。
            items = cast(list[object], value)
            for item in items:
                item_text = self._extract_reasoning_text(item)
                if item_text is not None:
                    text_parts.append(item_text)
            if text_parts:
                return "\n".join(text_parts)
            return None
        if isinstance(value, dict):
            raw_items = cast(dict[str, object], value)
            for key in REASONING_TEXT_KEYS:
                item_text = self._extract_reasoning_text(raw_items.get(key))
                if item_text is not None:
                    return item_text
            return None
        return None

    @override
    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> str:
        """调用 OpenAI Chat Completions 接口获取文本响应。"""
        chat_messages = self._format_chat_messages(messages)
        response = await self.client.chat.completions.create(
            model=model,
            messages=chat_messages,
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("OpenAI 兼容服务返回了空文本")
        return content

    @override
    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """调用 OpenAI Chat Completions 工具调用接口。"""
        if not tools:
            response = await self.client.chat.completions.create(
                model=model,
                messages=self._format_chat_messages(messages),
            )
            message = response.choices[0].message
            return LLMResponse(
                content=message.content,
                reasoning_content=self._extract_reasoning_content(message),
            )
        response = await self.client.chat.completions.create(
            model=model,
            messages=self._format_chat_messages(messages),
            tools=self._format_tools(tools),
            tool_choice=cast(ChatCompletionToolChoiceOptionParam, tool_choice),
            parallel_tool_calls=parallel_tool_calls,
        )
        message = response.choices[0].message
        tool_calls: list[LLMToolCall] = []
        for raw_tool_call in message.tool_calls or []:
            if raw_tool_call.type != "function":
                raise ValueError("当前仅支持 function 类型工具调用")
            arguments = self._parse_tool_arguments(raw_tool_call.function.arguments)
            tool_calls.append(
                LLMToolCall(
                    id=raw_tool_call.id,
                    name=raw_tool_call.function.name,
                    arguments=arguments,
                )
            )
        return LLMResponse(
            content=message.content,
            reasoning_content=self._extract_reasoning_content(message),
            tool_calls=tool_calls,
        )

    def _parse_tool_arguments(self, raw_arguments: str) -> JsonObject:
        """解析模型返回的工具参数 JSON。"""
        try:
            parsed = cast(object, json.loads(raw_arguments))
        except json.JSONDecodeError as exc:
            raise ValueError(f"工具参数不是合法 JSON: {raw_arguments}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        return cast(JsonObject, parsed)

    @override
    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        """使用标准 OpenAI 兼容图片接口生成图片，仅传递接口必需参数。"""
        if not message.text:
            raise ValueError("提示词为空请重新输入")

        if message.image:
            image_files = self._build_image_files(message.image)
            response = await self.client.images.edit(
                image=image_files,
                model=model,
                prompt=message.text,
            )
        else:
            response = await self.client.images.generate(
                model=model,
                prompt=message.text,
            )
        return self._extract_base64_image(response)
