"""协议边界和共享工具契约。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import uuid4

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
    async def call(
        self, action: str, params: JsonObject, *, timeout: float | None = None
    ) -> JsonValue: ...


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
    loaded_tools: set[str] = field(default_factory=set)
    # 实际进入 handler 的工具，按最近调用顺序保留；发现与加载不算实际使用。
    used_tools: list[str] = field(default_factory=list)
    sent_messages: list[JsonObject] = field(default_factory=list)
    send_attempts: int = 0
    max_sends: int = 5
    unknown_effects: list[UnknownOperation] = field(default_factory=list)
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
class ToolDefinition:
    name: str
    description: str
    category: str
    parameters: JsonObject
    handler: ToolHandler
    effects: bool = False
    scope: Literal["both", "group", "private", "account"] = "both"
    cross_scope: bool = False
    model: type[BaseModel] | None = None
    requires: tuple[str, ...] = ()
    # 面向控制台的简短名称；模型看到的仍是 description。
    title: str = ""
    # 精准检索别名，不混入通用权限/安全说明，也不追加到模型描述。
    search_terms: tuple[str, ...] = ()

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


def schema(model: type[BaseModel]) -> JsonObject:
    return JSON_OBJECT.validate_python(model.model_json_schema())
