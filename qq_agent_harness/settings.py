"""唯一配置模型；权限不包含任何发起者角色或个人 ACL。"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import Field, JsonValue, ValidationInfo, field_validator, model_validator

from .contracts import ConversationScope, Model

# 这些标识会拼入请求地址或请求体，只接受各自的规范字符集。
_PROJECT_ID = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_VOICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_LANGUAGE_CODE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")


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
    categories: dict[str, bool] = Field(default_factory=lambda: {"messages": True, "members": True})
    tools: dict[str, bool] = Field(default_factory=dict)
    parameters: dict[str, dict[str, ParameterRule]] = Field(default_factory=dict)
    allow_at_all: bool = False
    cross_groups: list[str] = Field(default_factory=list)
    cross_private: bool = False
    max_sends_per_turn: int = Field(default=5, ge=1)


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
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=120, gt=0)
    max_retries: int = Field(default=10, ge=0)
    proxy: str | None = None
    store_supported: bool = True

    def endpoint_base(self) -> str:
        stripped = self.base_url.strip().rstrip("/")
        if not stripped:
            return ""
        parsed = urlsplit(stripped)
        if parsed.scheme and parsed.netloc and parsed.path in ("", "/"):
            return f"{stripped}/v1"
        return stripped

    def request_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if not any(key.lower() == "authorization" for key in headers):
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


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


class ImageSettings(Model):
    """独立于会话服务商的生图接口配置。"""

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=0)
    proxy: str | None = None

    def endpoint_base(self) -> str:
        stripped = self.base_url.strip().rstrip("/")
        if not stripped:
            return ""
        parsed = urlsplit(stripped)
        if parsed.scheme and parsed.netloc and parsed.path in ("", "/"):
            return f"{stripped}/v1"
        return stripped

    def request_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if not any(key.lower() == "authorization" for key in headers):
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


@dataclass(frozen=True)
class ServiceAccount:
    """服务账号密钥中签发访问令牌所需的字段。"""

    client_email: str
    private_key: str = field(repr=False)
    private_key_id: str = ""
    project_id: str = ""


def parse_service_account(text: str) -> ServiceAccount:
    """校验服务账号密钥 JSON；错误只说明哪个字段不符合要求，不回显任何取值。"""
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("服务账号凭据不是有效的 JSON") from None
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise ValueError("只支持 type 为 service_account 的服务账号密钥 JSON")
    if data.get("universe_domain", "googleapis.com") != "googleapis.com":
        raise ValueError("只支持 googleapis.com 上的服务账号")
    email, key, key_id, project = (
        data.get(name) for name in ("client_email", "private_key", "private_key_id", "project_id")
    )
    if not isinstance(email, str) or "@" not in email:
        raise ValueError("服务账号 JSON 缺少有效的 client_email")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("服务账号 JSON 缺少 private_key")
    if not isinstance(key_id, str | None) or not isinstance(project, str | None):
        raise ValueError("服务账号 JSON 的 private_key_id 或 project_id 格式无效")
    if project and not _PROJECT_ID.fullmatch(project):
        raise ValueError("服务账号 JSON 的 project_id 格式无效")
    try:
        loaded = load_pem_private_key(key.encode("utf-8"), password=None)
    except ValueError, TypeError, UnsupportedAlgorithm:
        raise ValueError("服务账号私钥无法解析") from None
    if not isinstance(loaded, RSAPrivateKey):
        raise ValueError("服务账号私钥不是 RSA 密钥")
    return ServiceAccount(email, key, key_id or "", project or "")


class VertexTTSSettings(Model):
    """Vertex（现名 Gemini Enterprise Agent Platform）连接；凭据是服务账号密钥 JSON 原文。"""

    credentials: str = ""
    # 留空时使用服务账号所属项目。
    project_id: str = Field(default="", validate_default=True)
    location: Literal["global", "us", "eu"] = "global"

    @field_validator("credentials")
    @classmethod
    def valid_credentials(cls, value: str) -> str:
        if value.strip():
            parse_service_account(value)
        return value

    @field_validator("project_id")
    @classmethod
    def valid_project(cls, value: str, info: ValidationInfo) -> str:
        if value:
            if not _PROJECT_ID.fullmatch(value):
                raise ValueError("项目 ID 应为 6–30 位小写字母、数字或连字符，以字母开头")
            return value
        credentials = info.data.get("credentials")
        if isinstance(credentials, str) and credentials.strip():
            data = json.loads(credentials)
            if not (isinstance(data, dict) and data.get("project_id")):
                raise ValueError("服务账号 JSON 没有 project_id，请填写项目 ID")
        return value

    def account(self) -> ServiceAccount:
        return parse_service_account(self.credentials)


class TTSSettings(Model):
    """独立于会话服务商的语音合成配置；protocol 决定使用的连接子表和请求格式。"""

    enabled: bool = False
    protocol: Literal["vertex_gemini"] = "vertex_gemini"
    model: str = ""
    # 留空时不指定音色与语言，由接口使用默认音色并自动识别语言。
    voice: str = ""
    language_code: str = ""
    timeout: float = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=0)
    proxy: str | None = None
    vertex: VertexTTSSettings = Field(default_factory=VertexTTSSettings)

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if value and not _MODEL_ID.fullmatch(value):
            raise ValueError("模型名称只能包含字母、数字、点、下划线和连字符")
        return value

    @field_validator("voice")
    @classmethod
    def valid_voice(cls, value: str) -> str:
        if value and not _VOICE_ID.fullmatch(value):
            raise ValueError("音色只能包含字母、数字、下划线和连字符")
        return value

    @field_validator("language_code")
    @classmethod
    def valid_language(cls, value: str) -> str:
        if value and not _LANGUAGE_CODE.fullmatch(value):
            raise ValueError("语言代码应为 BCP-47 格式，例如 cmn-CN、en-US")
        return value

    def unavailable_reason(self) -> str:
        if not self.enabled:
            return "尚未启用语音合成"
        if not self.vertex.credentials.strip():
            return "语音合成尚未导入服务账号"
        if not self.model:
            return "语音合成尚未选择模型"
        return ""


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
    image: ImageSettings = Field(default_factory=ImageSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
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
