"""OpenAI Responses 格式适配，使用本轮输出项续接工具调用。"""

import base64
import json
from typing import Literal, cast, override

from openai import AsyncOpenAI
from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseInputParam,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ToolChoiceFunctionParam,
    ToolParam,
)
from pydantic import TypeAdapter, ValidationError

from app.models import JsonObject, JsonValue
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

OUTPUT_ITEM_ADAPTER: TypeAdapter[
    ResponseOutputMessage | ResponseFunctionToolCall | ResponseReasoningItem
] = TypeAdapter(ResponseOutputMessage | ResponseFunctionToolCall | ResponseReasoningItem)
JSON_OBJECT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ResponsesService(LLMProvider):
    """通过无服务端状态的 Responses 请求访问模型。"""

    def __init__(self, client: AsyncOpenAI) -> None:
        self.client: AsyncOpenAI = client

    def _format_input(self, messages: list[ChatMessage]) -> ResponseInputParam:
        """将消息和工具结果转成 Items，模型原始输出项按原顺序重放。"""
        items: list[JsonObject] = []
        for message in messages:
            if message.video:
                raise LLMResponseError(
                    "unsupported_video_input", "Responses 接口不支持当前视频输入格式。"
                )
            if message.response_items:
                items.extend(message.response_items)
                continue
            if message.role == "tool":
                if message.tool_call_id is None or message.text is None:
                    raise ValueError("工具结果缺少 tool_call_id 或 text")
                items.append({
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.text,
                })
                continue
            if message.image:
                content: list[JsonObject] = []
                if message.text is not None:
                    content.append({"type": "input_text", "text": message.text})
                for image in message.image:
                    mime_type = detect_mime_type(image)
                    encoded = base64.b64encode(image).decode("ascii")
                    image_item: JsonObject = {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{encoded}",
                    }
                    if message.image_detail is not None:
                        image_item["detail"] = message.image_detail
                    content.append(image_item)
                items.append({
                    "role": message.role,
                    "content": cast(list[JsonValue], content),
                })
            elif message.text is not None:
                items.append({"role": message.role, "content": message.text})
            for tool_call in message.tool_calls or []:
                items.append({
                    "type": "function_call",
                    "call_id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                })
        return cast(ResponseInputParam, cast(object, items))

    def _format_tools(self, tools: list[LLMToolDefinition]) -> list[ToolParam]:
        """Responses 的 function 字段与 type 位于同一层。"""
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": cast(dict[str, object], tool.parameters),
                "strict": tool.strict,
            }
            for tool in tools
        ]

    def _format_tool_choice(
        self, choice: LLMToolChoice
    ) -> Literal["auto", "none", "required"] | ToolChoiceFunctionParam:
        """统一的指定函数选项按当前接口格式编码。"""
        if isinstance(choice, str):
            return choice
        function = choice.get("function")
        if choice.get("type") != "function" or not isinstance(function, dict):
            raise ValueError("tool_choice 必须指定一个函数名称")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool_choice 缺少有效函数名称")
        return {"type": "function", "name": name}

    def _decode_response(self, response: Response, *, tools_allowed: bool) -> LLMResponse:
        """只交付正式输出，协议状态完整保留给本轮下一次请求。"""
        if (
            getattr(response, "status", None) != "completed"
            or getattr(response, "error", None) is not None
            or getattr(response, "incomplete_details", None) is not None
        ):
            raise LLMResponseError(
                "incomplete_response", "Responses 请求未完整结束，不能使用正文和工具调用。"
            )
        raw_output: object = getattr(response, "output", None)
        if not isinstance(raw_output, list) or not raw_output:
            raise LLMResponseError("missing_output", "Responses 响应缺少输出项")
        output_items: list[JsonObject] = []
        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for raw_item in cast(list[object], raw_output):
            if not isinstance(
                raw_item, (ResponseOutputMessage, ResponseFunctionToolCall, ResponseReasoningItem)
            ):
                raise LLMResponseError("unsupported_output", "Responses 返回了未请求的输出类型")
            try:
                raw_data = raw_item.model_dump(mode="json", exclude_unset=True, warnings=False)
                item = OUTPUT_ITEM_ADAPTER.validate_python(raw_data, strict=True)
                output_items.append(JSON_OBJECT_ADAPTER.validate_python(raw_data, strict=True))
            except (ValidationError, ValueError, TypeError):
                raise LLMResponseError("invalid_output", "Responses 输出项结构无效") from None
            if item.status not in {None, "completed"}:
                raise LLMResponseError("incomplete_output", "Responses 输出项尚未完整生成")
            if isinstance(item, ResponseFunctionToolCall):
                try:
                    arguments = JSON_OBJECT_ADAPTER.validate_json(item.arguments, strict=True)
                except (ValidationError, ValueError):
                    raise LLMResponseError(
                        "invalid_tool_arguments", "模型工具参数必须是合法 JSON 对象"
                    ) from None
                tool_calls.append(LLMToolCall(
                    id=item.call_id, name=item.name, arguments=arguments
                ))
            elif isinstance(item, ResponseOutputMessage):
                for content in item.content:
                    if isinstance(content, ResponseOutputText):
                        text_parts.append(content.text)
                    else:
                        text_parts.append(content.refusal)
        decoded = LLMResponse(
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            response_items=output_items,
        )
        validate_response(decoded, tools_allowed=tools_allowed)
        if not (decoded.content or "").strip() and not decoded.tool_calls:
            raise LLMResponseError("empty_response", "Responses 没有返回可交付正文或工具调用")
        return decoded

    @override
    async def get_ai_response(self, messages: list[ChatMessage], model: str) -> str:
        response = await self.client.responses.create(
            model=model,
            input=self._format_input(messages),
            store=False,
            include=["reasoning.encrypted_content"],
        )
        content = self._decode_response(response, tools_allowed=False).content
        if content is None:
            raise LLMResponseError("empty_response", "Responses 没有返回可交付正文")
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
        if not tools:
            response = await self.client.responses.create(
                model=model,
                input=self._format_input(messages),
                store=False,
                include=["reasoning.encrypted_content"],
            )
        else:
            response = await self.client.responses.create(
                model=model,
                input=self._format_input(messages),
                tools=self._format_tools(tools),
                tool_choice=self._format_tool_choice(tool_choice),
                parallel_tool_calls=parallel_tool_calls,
                store=False,
                include=["reasoning.encrypted_content"],
            )
        return self._decode_response(response, tools_allowed=bool(tools) and tool_choice != "none")
