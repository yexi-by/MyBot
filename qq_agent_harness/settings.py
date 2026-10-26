"""唯一配置模型；权限不包含任何发起者角色或个人 ACL。"""

from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .contracts import ConversationScope, Model


class ParameterRule(Model):
    minimum: float | None = None
    maximum: float | None = None
    allowed: list[JsonValue] | None = None
    max_length: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def ordered_range(self) -> ParameterRule:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("参数约束下限不能大于上限")
        return self


class Policy(Model):
    categories: dict[str, bool] = Field(
        default_factory=lambda: {"messages.read": True, "messages.send": True, "members.read": True}
    )
    tools: dict[str, bool] = Field(default_factory=dict)
    parameters: dict[str, dict[str, ParameterRule]] = Field(default_factory=dict)
    allow_at_all: bool = False
    cross_groups: list[str] = Field(default_factory=list)
    cross_private: bool = False
    max_sends_per_turn: int = Field(default=5, ge=1, le=100)

    @model_validator(mode="after")
    def builtins_are_not_overrides(self) -> Policy:
        if "harness" in self.categories or any(
            name.startswith("harness__") for name in (*self.tools, *self.parameters)
        ):
            raise ValueError("工具发现和加载属于运行内置能力，不能单独覆盖权限")
        return self


class ConversationSettings(Model):
    enabled: bool = False
    keywords: list[str] = Field(default_factory=list)
    policy: Policy = Field(default_factory=Policy)


class ProviderSettings(Model):
    protocol: Literal["chat_completions", "responses"] = "responses"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    max_context_tokens: int = Field(default=128_000, ge=4096)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    timeout: float = Field(default=120, gt=0)
    proxy: str | None = None
    modalities: list[Literal["text", "image", "audio", "video"]] = Field(
        default_factory=lambda: ["text", "image"]
    )
    store_supported: bool = True


class NapCatSettings(Model):
    token: str = ""
    expected_bot_id: str = ""
    action_timeout: float = Field(default=30, gt=0)
    media_timeout: float = Field(default=30, gt=0)
    media_max_bytes: int = Field(default=50_000_000, gt=0)
    media_concurrency: int = Field(default=4, ge=1, le=32)


class RuntimeSettings(Model):
    concurrent_conversations: int = Field(default=4, ge=1, le=32)
    max_model_rounds: int = Field(default=20, ge=1, le=100)
    turn_timeout: float = Field(default=600, gt=0)
    history_count: int = Field(default=60, ge=0, le=300)
    max_pending_conversations: int = Field(default=100, ge=1, le=1000)
    max_pending_messages: int = Field(default=20, ge=1, le=100)
    tool_result_char_limit: int = Field(default=24_000, ge=1000)
    activity_limit: int = Field(default=200, ge=10, le=2000)


class MCPSettings(Model):
    enabled: bool = False
    transport: Literal["stdio", "streamable_http"] = "streamable_http"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=60, gt=0)
    read_only_tools: list[str] = Field(default_factory=list)


class SandboxSettings(Model):
    enabled: bool = False
    runner_url: str = "http://127.0.0.1:6060"
    runner_token: str = ""
    quota_bytes: int = Field(default=50_000_000_000, ge=1_000_000)
    cpus: float = Field(default=2, gt=0)
    memory_bytes: int = Field(default=4_294_967_296, ge=134_217_728)
    pids_limit: int = Field(default=256, ge=16)
    command_timeout: float = Field(default=120, gt=0, le=600)
    output_limit: int = Field(default=1_048_576, ge=1024, le=16_777_216)


class ServerSettings(Model):
    host: str = "0.0.0.0"
    port: int = Field(default=6055, ge=1, le=65535)


class HarnessSettings(Model):
    server: ServerSettings = Field(default_factory=ServerSettings)
    napcat: NapCatSettings = Field(default_factory=NapCatSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    active_provider: str = ""
    groups: dict[str, ConversationSettings] = Field(default_factory=dict)
    private: ConversationSettings = Field(default_factory=ConversationSettings)
    mcp: dict[str, MCPSettings] = Field(default_factory=dict)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    persona_file: str = "persona.md"
    knowledge_files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_references(self) -> HarnessSettings:
        if self.active_provider and self.active_provider not in self.providers:
            raise ValueError("当前使用的模型连接不存在")
        for name in self.persona_paths:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
                raise ValueError("提示词和知识库路径必须位于配置目录内")
        return self

    @property
    def persona_paths(self) -> list[str]:
        return [self.persona_file, *self.knowledge_files]

    def conversation(self, scope: ConversationScope) -> ConversationSettings:
        return (
            self.groups.get(scope.peer_id, ConversationSettings())
            if scope.kind == "group"
            else self.private
        )
