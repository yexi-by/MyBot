"""OpenAI 协议 LLM 服务实现。"""

import base64
import json
import re
from typing import Final, cast, override

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
    ChatCompletionToolParam,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_function_tool_call import Function
from openai.types.images_response import ImagesResponse
from pydantic import ValidationError

from app.models import JsonObject
from app.utils.file_type import detect_mime_type

from ..base import LLMProvider
from ..errors import LLMResponseError
from ..response_validation import validate_response
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
FINISH_REASON_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")


class OpenAIService(LLMProvider):
    """通过 OpenAI Chat Completions 和 Images 协议访问模型服务。"""

    def __init__(self, client: AsyncOpenAI) -> None:
        """保存 OpenAI 异步客户端。"""
        self.client: AsyncOpenAI = client

    def _decode_response(
        self, response: ChatCompletion, *, tools_allowed: bool
    ) -> LLMResponse:
        """在协议入口收窄上游数据，分类异常不携带正文或原始工具参数。"""
        raw_choices: object = getattr(response, "choices", None)
        if not isinstance(raw_choices, list) or not raw_choices:
            raise LLMResponseError("missing_choices", "模型响应缺少候选结果")
        choice = cast(list[object], raw_choices)[0]
        if not isinstance(choice, Choice):
            raise LLMResponseError("invalid_choice", "模型响应候选结构无效")
        message: object = getattr(choice, "message", None)
        if not isinstance(message, ChatCompletionMessage):
            raise LLMResponseError("invalid_message", "模型响应消息结构无效")
        content: object = getattr(message, "content", None)
        if content is not None and not isinstance(content, str):
            raise LLMResponseError("invalid_content", "模型响应正文类型无效")
        if getattr(message, "function_call", None) is not None:
            raise LLMResponseError(
                "unsupported_tool_call", "模型响应使用了未支持的旧工具调用格式"
            )
        finish_reason = self._normalize_finish_reason(
            getattr(choice, "finish_reason", None)
        )
        native_finish_reason = self._normalize_finish_reason(
            getattr(choice, "native_finish_reason", None)
        )
        raw_calls: object = getattr(message, "tool_calls", None)
        if raw_calls is not None and not isinstance(raw_calls, list):
            raise LLMResponseError("invalid_tool_calls", "模型响应工具调用列表无效")
        tool_calls: list[LLMToolCall] = []
        for raw_call in cast(list[object], raw_calls or []):
            if (
                not isinstance(raw_call, ChatCompletionMessageFunctionToolCall)
                or getattr(raw_call, "type", None) != "function"
            ):
                raise LLMResponseError(
                    "unsupported_tool_call", "模型响应工具调用类型无效"
                )
            function: object = getattr(raw_call, "function", None)
            if not isinstance(function, Function):
                raise LLMResponseError("invalid_tool_call", "模型响应工具函数结构无效")
            call_id: object = getattr(raw_call, "id", None)
            name: object = getattr(function, "name", None)
            if not isinstance(call_id, str) or not call_id.strip():
                raise LLMResponseError("invalid_tool_call_id", "模型响应工具调用缺少有效 ID")
            if not isinstance(name, str) or not name.strip():
                raise LLMResponseError("invalid_tool_name", "模型响应工具调用缺少有效名称")
            arguments = self._parse_tool_arguments(
                getattr(function, "arguments", None)
            )
            try:
                tool_call = LLMToolCall(id=call_id, name=name, arguments=arguments)
            except ValidationError:
                raise LLMResponseError(
                    "invalid_tool_arguments", "模型响应工具参数结构无效"
                ) from None
            tool_calls.append(tool_call)
        decoded = LLMResponse(
            content=content,
            reasoning_content=self._extract_reasoning_content(message),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            native_finish_reason=native_finish_reason,
        )
        validate_response(decoded, tools_allowed=tools_allowed)
        return decoded

    def _normalize_finish_reason(self, value: object) -> str | None:
        """只保留短 ASCII 状态标记，防止上游错误正文混入状态或日志。"""
        if value is None:
            return None
        if not isinstance(value, str) or FINISH_REASON_PATTERN.fullmatch(value) is None:
            raise LLMResponseError("invalid_finish_reason", "模型响应结束状态格式无效")
        return value.lower()

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
            if msg.image or msg.video:
                if msg.text:
                    content_items.append({"type": "text", "text": msg.text})
                for image_bytes in msg.image or []:
                    mime_type = detect_mime_type(image_bytes)
                    image_data = base64.b64encode(image_bytes).decode("utf-8")
                    base64_image = f"data:{mime_type};base64,{image_data}"
                    image_url: dict[str, object] = {"url": base64_image}
                    if msg.image_detail is not None:
                        image_url["detail"] = msg.image_detail
                    content_items.append(
                        {
                            "type": "image_url",
                            "image_url": image_url,
                        }
                    )
                for video_bytes in msg.video or []:
                    mime_type = detect_mime_type(video_bytes)
                    if not mime_type.startswith("video/"):
                        raise ValueError("视频附件必须是可识别的视频格式")
                    video_data = base64.b64encode(video_bytes).decode("ascii")
                    content_items.append({
                        "type": "video_url",
                        "video_url": {"url": f"data:{mime_type};base64,{video_data}"},
                    })
            raw_message: dict[str, object] = {
                "role": msg.role,
                "content": content_items if msg.image or msg.video else msg.text,
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
        """提取模型服务显式返回的可展示思考内容。"""
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
            # reasoning 明细数组来自服务端扩展字段，入口处收窄为 object 列表逐项解析。
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
        content = self._decode_response(response, tools_allowed=False).content
        if content is None:
            raise ValueError("OpenAI 协议服务返回了空文本")
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
            return self._decode_response(response, tools_allowed=False)
        response = await self.client.chat.completions.create(
            model=model,
            messages=self._format_chat_messages(messages),
            tools=self._format_tools(tools),
            tool_choice=cast(ChatCompletionToolChoiceOptionParam, tool_choice),
            parallel_tool_calls=parallel_tool_calls,
        )
        return self._decode_response(response, tools_allowed=tool_choice != "none")

    def _parse_tool_arguments(self, raw_arguments: object) -> JsonObject:
        """解析模型返回的工具参数 JSON。"""
        if not isinstance(raw_arguments, str):
            raise LLMResponseError("invalid_tool_arguments", "模型响应工具参数必须是 JSON 字符串")
        try:
            parsed = cast(object, json.loads(raw_arguments))
        except (ValueError, RecursionError):
            raise LLMResponseError(
                "invalid_tool_arguments", "模型响应工具参数不是合法 JSON"
            ) from None
        if not isinstance(parsed, dict):
            raise LLMResponseError("invalid_tool_arguments", "模型响应工具参数必须是 JSON 对象")
        return cast(JsonObject, parsed)

    @override
    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        """使用 OpenAI Images 协议生成或编辑图片。"""
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
