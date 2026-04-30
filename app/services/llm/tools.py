"""LLM 工具注册与执行循环。"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast, override

from pydantic import BaseModel

from app.models import JsonObject, JsonValue, to_json_value

from .schemas import ChatMessage, LLMToolDefinition, LLMToolExecutor

type ToolHandler = Callable[[JsonObject], Awaitable[JsonValue]]


@dataclass
class RegisteredLLMTool:
    """本地注册的 LLM 工具。"""

    definition: LLMToolDefinition
    parameters_model: type[BaseModel]
    handler: ToolHandler


class LLMToolRegistry(LLMToolExecutor):
    """统一管理本地工具。"""

    def __init__(self) -> None:
        """初始化空工具注册表。"""
        self._tools: dict[str, RegisteredLLMTool] = {}

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        parameters_model: type[BaseModel],
        handler: ToolHandler,
        strict: bool = True,
    ) -> None:
        """使用 Pydantic 参数模型注册本地工具。"""
        raw_schema = cast(
            JsonObject, parameters_model.model_json_schema(mode="validation")
        )
        schema = self._build_strict_parameters_schema(raw_schema)
        definition = LLMToolDefinition(
            name=name,
            description=description,
            parameters=schema,
            strict=strict,
        )
        if name in self._tools:
            raise ValueError(f"LLM 工具已存在: {name}")
        self._tools[name] = RegisteredLLMTool(
            definition=definition,
            parameters_model=parameters_model,
            handler=handler,
        )

    def _build_strict_parameters_schema(self, schema: JsonObject) -> JsonObject:
        """将 Pydantic 参数 schema 转成 OpenAI strict function 要求的格式。"""
        definitions = self._collect_schema_definitions(schema=schema)
        normalized_schema = self._normalize_schema_node(
            value=schema,
            definitions=definitions,
        )
        if not isinstance(normalized_schema, dict):
            raise TypeError("工具参数 schema 必须是 JSON 对象")
        return normalized_schema

    def _collect_schema_definitions(self, *, schema: JsonObject) -> dict[str, JsonObject]:
        """读取 Pydantic 生成的本地 `$defs`，用于后续展开 `$ref`。"""
        raw_definitions = schema.get("$defs")
        if not isinstance(raw_definitions, dict):
            return {}
        definitions: dict[str, JsonObject] = {}
        # JSON Schema 的 `$defs` 是第三方结构，入口处逐项收窄为字符串键对象。
        raw_items = cast(dict[str, JsonValue], raw_definitions)
        for key, value in raw_items.items():
            if isinstance(value, dict):
                definitions[key] = cast(JsonObject, value)
        return definitions

    def _normalize_schema_node(
        self, *, value: JsonValue, definitions: dict[str, JsonObject]
    ) -> JsonValue:
        """递归补齐 object 节点的 required 与 additionalProperties。"""
        if isinstance(value, list):
            return [
                self._normalize_schema_node(value=item, definitions=definitions)
                for item in value
            ]
        if not isinstance(value, dict):
            return value

        value = self._resolve_schema_reference(value=value, definitions=definitions)
        normalized: JsonObject = {}
        for key, item in value.items():
            if key in {"$defs", "default"}:
                continue
            normalized[key] = self._normalize_schema_node(
                value=item,
                definitions=definitions,
            )

        properties = normalized.get("properties")
        if isinstance(properties, dict):
            # JSON Schema 的 properties 是第三方结构，进入边界后立即收窄为字符串键对象。
            property_map = cast(JsonObject, properties)
            normalized["required"] = list(property_map.keys())
            normalized["additionalProperties"] = False
        return normalized

    def _resolve_schema_reference(
        self, *, value: JsonObject, definitions: dict[str, JsonObject]
    ) -> JsonObject:
        """展开 Pydantic 本地 `$ref`，让 strict schema 仅包含可提交字段。"""
        raw_ref = value.get("$ref")
        if not isinstance(raw_ref, str) or not raw_ref.startswith("#/$defs/"):
            return value
        definition_key = raw_ref.removeprefix("#/$defs/")
        definition = definitions.get(definition_key)
        if definition is None:
            raise ValueError(f"工具参数 schema 引用了未知定义: {raw_ref}")
        merged: JsonObject = dict(definition)
        for key, item in value.items():
            if key == "$ref":
                continue
            merged[key] = item
        return merged

    @override
    def list_tools(self) -> list[LLMToolDefinition]:
        """返回所有已注册工具定义。"""
        return [tool.definition for tool in self._tools.values()]

    @override
    async def call_tool(self, name: str, arguments: JsonObject) -> JsonValue:
        """调用指定本地工具。"""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"未知 LLM 工具: {name}")
        validated_arguments = self._validate_tool_arguments(tool, arguments)
        result = await tool.handler(validated_arguments)
        return result

    def _validate_tool_arguments(
        self, tool: RegisteredLLMTool, arguments: JsonObject
    ) -> JsonObject:
        """按工具参数模型校验并收窄模型生成的参数。"""
        validated_model = tool.parameters_model.model_validate(arguments)
        raw_dump = cast(object, validated_model.model_dump(mode="json"))
        if not isinstance(raw_dump, dict):
            raise ValueError(f"LLM 工具参数模型未生成 JSON 对象: {tool.definition.name}")
        return cast(JsonObject, raw_dump)


class CompositeToolExecutor(LLMToolExecutor):
    """按顺序组合多个工具执行器。"""

    def __init__(self, executors: list[LLMToolExecutor]) -> None:
        """保存工具执行器列表。"""
        self._executors: list[LLMToolExecutor] = executors

    @override
    def list_tools(self) -> list[LLMToolDefinition]:
        """合并所有工具定义并检查重名。"""
        tools: list[LLMToolDefinition] = []
        seen: set[str] = set()
        for executor in self._executors:
            for tool in executor.list_tools():
                if tool.name in seen:
                    raise ValueError(f"LLM 工具名重复: {tool.name}")
                seen.add(tool.name)
                tools.append(tool)
        return tools

    @override
    async def call_tool(self, name: str, arguments: JsonObject) -> JsonValue:
        """按执行器顺序查找并调用工具。"""
        for executor in self._executors:
            tool_names = {tool.name for tool in executor.list_tools()}
            if name in tool_names:
                return await executor.call_tool(name=name, arguments=arguments)
        raise KeyError(f"未知 LLM 工具: {name}")


def tool_result_to_text(result: JsonValue) -> str:
    """将工具执行结果转换为 tool 消息文本。"""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def build_tool_result_message(tool_call_id: str, result: JsonValue) -> ChatMessage:
    """构造 OpenAI 工具结果消息。"""
    return ChatMessage(
        role="tool",
        text=tool_result_to_text(to_json_value(result)),
        tool_call_id=tool_call_id,
    )
