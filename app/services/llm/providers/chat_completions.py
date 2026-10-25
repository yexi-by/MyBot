"""OpenAI Chat Completions 格式适配。"""

import base64
import json
from typing import cast, override

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
from pydantic import TypeAdapter, ValidationError

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

EXTRA_CONTENT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ChatCompletionsService(LLMProvider):
    """通过 OpenAI Chat Completions 协议访问模型服务。"""

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
        refusal: object = getattr(message, "refusal", None)
        if refusal is not None and not isinstance(refusal, str):
            raise LLMResponseError("invalid_refusal", "模型拒绝回复类型无效")
        if not content:
            content = refusal
        if getattr(message, "function_call", None) is not None:
            raise LLMResponseError(
                "unsupported_tool_call", "模型响应使用了未支持的旧工具调用格式"
            )
        finish_reason: object = getattr(choice, "finish_reason", None)
        if not isinstance(finish_reason, str) or finish_reason not in {"stop", "tool_calls"}:
            raise LLMResponseError(
                "invalid_finish_reason", "Chat Completions 响应未以完整状态结束"
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
                raw_extra_content: object = getattr(raw_call, "extra_content", None)
                extra_content = (
                    EXTRA_CONTENT_ADAPTER.validate_python(raw_extra_content, strict=True)
                    if raw_extra_content is not None else None
                )
                tool_call = LLMToolCall(
                    id=call_id, name=name, arguments=arguments,
                    extra_content=extra_content,
                )
            except ValidationError:
                raise LLMResponseError(
                    "invalid_tool_arguments", "模型响应工具参数或扩展状态结构无效"
                ) from None
            tool_calls.append(tool_call)
        decoded = LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
        validate_response(decoded, tools_allowed=tools_allowed)
        return decoded

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
                        **({"extra_content": tool_call.extra_content}
                           if tool_call.extra_content is not None else {}),
                    }
                    for tool_call in msg.tool_calls
                ]
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
