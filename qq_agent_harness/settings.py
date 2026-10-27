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
    max_sends_per_turn: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def builtins_are_not_overrides(self) -> Policy:
        if "harness" in self.categories or any(
            name.startswith("harness__") for name in (*self.tools, *self.parameters)
        ):
            raise ValueError("工具发现和加载属于运行内置能力，不能单独覆盖权限")
        return self


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
Modality = Literal["text", "image", "audio", "video"]


class ModelChoice(Model):
    """会话选择的模型；服务商只提供连接。"""

    provider: str = ""
    name: str = ""
    reasoning_effort: ReasoningEffort | None = None
    max_context_tokens: int = Field(default=128_000, ge=4096)


class SessionModelSettings(ModelChoice):
    # 上下文达到预算 80% 时压缩到预算的这一比例，须低于触发点。
    compaction_target_percent: int = Field(default=50, ge=10, le=70)
    modalities: list[Modality] = Field(default_factory=lambda: ["text", "image"])


class IdleCompactionSettings(Model):
    """会话闲置后把对话并入一份摘要；未选压缩模型时使用会话模型。"""

    enabled: bool = False
    after_minutes: int = Field(default=10, ge=1, le=10_080)
    min_tokens: int = Field(default=16_000, ge=0)
    model: ModelChoice = Field(default_factory=ModelChoice)


class ConversationSettings(Model):
    enabled: bool = False
    keywords: list[str] = Field(default_factory=list)
    model: SessionModelSettings = Field(default_factory=SessionModelSettings)
    idle_compaction: IdleCompactionSettings = Field(default_factory=IdleCompactionSettings)
    policy: Policy = Field(default_factory=Policy)


class ProviderSettings(Model):
    """服务商连接；模型名称和能力由各会话选择。"""

    protocol: Literal["chat_completions", "responses"] = "responses"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    timeout: float = Field(default=120, gt=0)
    proxy: str | None = None
    store_supported: bool = True


class ModelEndpoint(ProviderSettings):
    """运行时为会话解析出的请求设置：服务商连接加所选模型，不写入配置。"""

    model: str = ""
    reasoning_effort: ReasoningEffort | None = None
    max_context_tokens: int = Field(default=128_000, ge=4096)
    compaction_target_percent: int = Field(default=50, ge=10, le=70)
    modalities: list[Modality] = Field(default_factory=lambda: ["text", "image"])


class NapCatSettings(Model):
    token: str = ""
    expected_bot_id: str = ""
    action_timeout: float = Field(default=30, gt=0)
    media_timeout: float = Field(default=30, gt=0)
    media_max_bytes: int = Field(default=50_000_000, gt=0)
    media_concurrency: int = Field(default=4, ge=1)
    event_queue_size: int = Field(default=128, ge=1)


class RuntimeSettings(Model):
    concurrent_conversations: int = Field(default=4, ge=1)
    max_model_rounds: int = Field(default=20, ge=1)
    turn_timeout: float = Field(default=600, gt=0)
    history_count: int = Field(default=60, ge=0)
    max_pending_conversations: int = Field(default=100, ge=1)
    max_pending_messages: int = Field(default=20, ge=1)
    max_retained_sessions: int = Field(default=100, ge=1)
    max_retained_bytes: int = Field(default=268_435_456, ge=1)
    config_text_max_bytes: int = Field(default=2_000_000, ge=1)
    activity_limit: int = Field(default=200, ge=1)


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
    effect_tools: list[str] = Field(default_factory=list)


class SandboxSettings(Model):
    enabled: bool = False
    runner_url: str = "http://127.0.0.1:6060"
    runner_token: str = ""
    quota_bytes: int = Field(default=50_000_000_000, ge=1_000_000)
    cpus: float = Field(default=2, gt=0)
    memory_bytes: int = Field(default=4_294_967_296, ge=134_217_728)
    pids_limit: int = Field(default=256, ge=16)
    command_timeout: float = Field(default=120, gt=0)
    output_limit: int = Field(default=1_048_576, ge=1)
    transfer_max_bytes: int = Field(default=50_000_000, ge=1)
    max_pending_operations: int = Field(default=32, ge=1)


class ServerSettings(Model):
    host: str = "0.0.0.0"
    port: int = Field(default=6055, ge=1, le=65535)


class HarnessSettings(Model):
    server: ServerSettings = Field(default_factory=ServerSettings)
    napcat: NapCatSettings = Field(default_factory=NapCatSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    groups: dict[str, ConversationSettings] = Field(default_factory=dict)
    private: ConversationSettings = Field(default_factory=ConversationSettings)
    mcp: dict[str, MCPSettings] = Field(default_factory=dict)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    persona_file: str = "persona.md"
    knowledge_files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_references(self) -> HarnessSettings:
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

    def missing_providers(self) -> list[list[str]]:
        """引用了不存在服务商的字段路径。"""
        conversations = [
            (["private"], self.private),
            *((["groups", key], value) for key, value in self.groups.items()),
        ]
        return [
            [*base, *section, "provider"]
            for base, conversation in conversations
            for section, choice in (
                (["model"], conversation.model),
                (["idle_compaction", "model"], conversation.idle_compaction.model),
            )
            if choice.provider and choice.provider not in self.providers
        ]

    def session_endpoint(self, scope: ConversationScope) -> ModelEndpoint | None:
        choice = self.conversation(scope).model
        provider = self.providers.get(choice.provider)
        if provider is None or not choice.name:
            return None
        return ModelEndpoint(
            **provider.model_dump(),
            model=choice.name,
            reasoning_effort=choice.reasoning_effort,
            max_context_tokens=choice.max_context_tokens,
            compaction_target_percent=choice.compaction_target_percent,
            modalities=list(choice.modalities),
        )

    def compaction_endpoint(self, scope: ConversationScope) -> ModelEndpoint | None:
        """闲时压缩模型；没有完整选择时沿用会话模型，与轮内压缩一样不发送思考等级。"""
        choice = self.conversation(scope).idle_compaction.model
        provider = self.providers.get(choice.provider)
        if provider is not None and choice.name:
            return ModelEndpoint(
                **provider.model_dump(),
                model=choice.name,
                reasoning_effort=choice.reasoning_effort,
                max_context_tokens=choice.max_context_tokens,
            )
        session = self.session_endpoint(scope)
        return session.model_copy(update={"reasoning_effort": None}) if session else None
