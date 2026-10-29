"""协议边界和共享工具契约。"""

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import unquote
from uuid import uuid4

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

JsonObject = dict[str, JsonValue]
JSON_OBJECT = TypeAdapter(JsonObject)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ConversationScope(Model):
    bot_id: str
    kind: Literal["group", "private"]
    peer_id: str

    @property
    def key(self) -> str:
        return f"{self.bot_id}:{self.kind}:{self.peer_id}"


class Message(Model):
    scope: ConversationScope
    message_id: str
    sender_id: str
    sender_name: str = ""
    time: int = 0
    segments: list[JsonObject] = Field(default_factory=list)

    @property
    def text(self) -> str:
        parts: list[str] = []
        for segment in self.segments:
            data = segment.get("data")
            if segment.get("type") == "text" and isinstance(data, dict):
                parts.append(str(data.get("text", "")))
        return "".join(parts)


class ToolResult(Model):
    status: Literal["ok", "error", "unknown", "cancelled"]
    data: JsonValue = None
    error_type: str | None = None
    message: str = ""

    @classmethod
    def success(cls, data: JsonValue = None) -> ToolResult:
        return cls(status="ok", data=data)

    @classmethod
    def failure(cls, message: str, error_type: str = "ToolError") -> ToolResult:
        return cls(status="error", error_type=error_type, message=message)


class NapCatConnection(Protocol):
    """协议入口负责把 QQ/NapCat 标识规范化为字符串。"""

    async def call(
        self, action: str, params: JsonObject, *, timeout: float | None = None
    ) -> JsonValue: ...


@dataclass(frozen=True)
class GeneratedImage:
    id: str
    content: bytes
    mime_type: str


@dataclass
class UnknownOperation:
    tool: str
    arguments: JsonObject
    target: ConversationScope
    id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class ToolContext:
    scope: ConversationScope
    turn_id: str
    napcat: NapCatConnection
    cancelled: Callable[[], bool] = lambda: False
    target_scope: ConversationScope | None = None
    sent_messages: list[JsonObject] = field(default_factory=list)
    send_attempts: int = 0
    max_sends: int = 5
    unknown_effects: list[UnknownOperation] = field(default_factory=list)
    generated_images: OrderedDict[str, GeneratedImage] = field(default_factory=OrderedDict)
    effect_submitted: bool = False
    read_workspace: Callable[[str], Awaitable[bytes]] | None = None
    write_workspace: Callable[[str, bytes], Awaitable[None]] | None = None
    recheck: Callable[[], ToolResult | None] = lambda: None
    check_tool: Callable[[str, JsonObject], ToolResult | None] = lambda _name, _args: (
        ToolResult.failure("聚合查询尚未建立授权上下文", "PermissionDenied")
    )

    @property
    def target(self) -> ConversationScope:
        return self.target_scope or self.scope


ToolHandler = Callable[[ToolContext, JsonObject], Awaitable[ToolResult]]


@dataclass(frozen=True)
class PreparedToolCall:
    arguments: JsonObject
    invoke: Callable[[ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolBinding:
    parameters: JsonObject
    prepare: Callable[[JsonObject], PreparedToolCall]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    category: str
    binding: ToolBinding
    effects: bool = False
    scope: Literal["both", "group", "private", "account"] = "both"
    cross_scope: bool = False
    requires: tuple[str, ...] = ()
    # 面向控制台的简短名称；模型看到的仍是 description。
    title: str = ""

    @property
    def parameters(self) -> JsonObject:
        return self.binding.parameters

    def public(self) -> JsonObject:
        return {
            "name": self.name,
            "title": self.title or self.name,
            "description": self.description,
            "category": self.category,
            "parameters": self.parameters,
            "effects": self.effects,
            "scope": self.scope,
            "requires": [name for name in self.requires],
        }


_SCHEMA_MAPS = {
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
    "dependentSchemas",
    "dependencies",
}
_SCHEMA_ARRAYS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_CHILDREN = {
    "additionalProperties",
    "unevaluatedProperties",
    "propertyNames",
    "contains",
    "items",
    "additionalItems",
    "unevaluatedItems",
    "not",
    "if",
    "then",
    "else",
    "contentSchema",
}
_SIMPLE_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}
_STRING_CONSTRAINTS = {"minLength", "maxLength", "pattern", "format"}


def _compact_nullable_union(node: JsonObject) -> None:
    branches = node.get("anyOf")
    if not isinstance(branches, list) or "type" in node:
        return
    types: list[JsonValue] = []
    constraints: JsonObject = {}
    for branch in branches:
        if not isinstance(branch, dict):
            return
        kind = branch.get("type")
        if not isinstance(kind, str) or kind not in _SIMPLE_TYPES or kind in types:
            return
        allowed = {"type"} | (_STRING_CONSTRAINTS if kind == "string" else set())
        if set(branch) - allowed:
            return
        types.append(kind)
        constraints.update({key: value for key, value in branch.items() if key != "type"})
    if "null" not in types or len(types) < 2 or constraints.keys() & node.keys():
        return
    # 字符串约束对 null 和其他类型不生效；复杂分支及父级同名约束不合并。
    node.pop("anyOf")
    node["type"] = types
    node.update(constraints)


def _references_union_branch(value: JsonValue) -> bool:
    if isinstance(value, list):
        return any(_references_union_branch(child) for child in value)
    if not isinstance(value, dict):
        return False
    if any(
        isinstance(reference := value.get(key), str) and "/anyOf/" in unquote(reference)
        for key in ("$ref", "$dynamicRef")
    ):
        return True
    return any(_references_union_branch(child) for child in value.values())


def _compact_schema_node(node: JsonObject, *, flatten_nullable: bool) -> None:
    node.pop("title", None)
    if node.get("default", False) is None:
        node.pop("default")
    if node.get("required") == []:
        node.pop("required")
    for key, value in list(node.items()):
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            for child in value.values():
                if isinstance(child, dict):
                    _compact_schema_node(child, flatten_nullable=flatten_nullable)
        elif key in _SCHEMA_CHILDREN and isinstance(value, dict):
            _compact_schema_node(value, flatten_nullable=flatten_nullable)
        elif (key in _SCHEMA_ARRAYS or key == "items") and isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    _compact_schema_node(child, flatten_nullable=flatten_nullable)
    if flatten_nullable:
        _compact_nullable_union(node)


def compact_schema(parameters: JsonObject) -> JsonObject:
    """精简冗余 schema 表达；只遍历 schema 位置，保留业务字段、数据和校验语义。"""
    result = deepcopy(parameters)
    # MCP 可用 JSON Pointer 直接引用 anyOf 子分支，此时必须保留原树形路径。
    _compact_schema_node(result, flatten_nullable=not _references_union_branch(parameters))
    return result


def schema(model: type[BaseModel]) -> JsonObject:
    return compact_schema(JSON_OBJECT.validate_python(model.model_json_schema()))


def model_binding[Arguments: BaseModel](
    model: type[Arguments],
    handler: Callable[[ToolContext, Arguments], Awaitable[ToolResult]],
) -> ToolBinding:
    parameters = schema(model)
    validator = Draft202012Validator(parameters)

    def prepare(arguments: JsonObject) -> PreparedToolCall:
        # schema 限制原始 JSON 类型，避免 Pydantic 将字符串/布尔值隐式转成数字。
        # 模型只在此构造一次，执行跨字段约束并补齐参与授权的默认值。
        validator.validate(arguments)
        request = model.model_validate(arguments)
        return PreparedToolCall(
            request.model_dump(mode="json"), lambda context: handler(context, request)
        )

    return ToolBinding(parameters, prepare)


def json_binding(parameters: JsonObject, handler: ToolHandler) -> ToolBinding:
    validator = Draft202012Validator(parameters)

    def prepare(arguments: JsonObject) -> PreparedToolCall:
        validator.validate(arguments)
        return PreparedToolCall(arguments, lambda context: handler(context, arguments))

    return ToolBinding(parameters, prepare)
