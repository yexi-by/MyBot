"""MyBot 统一配置模型。"""

from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import (
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from sqlalchemy import URL

from app.models import NapCatId, StrictModel

type AppEnvironment = Literal["development", "staging", "production", "test"]
type UvicornLogLevel = Literal[
    "critical", "error", "warning", "info", "debug", "trace"
]
type LogLevelName = Literal[
    "TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"
]


class ConfigModel(StrictModel):
    """不可原地修改的配置模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class AppConfig(ConfigModel):
    """应用自身元信息配置。"""

    name: str = "MyBot"
    environment: AppEnvironment = "production"


class ServerConfig(ConfigModel):
    """HTTP 与 WebSocket 服务监听配置。"""

    host: str = "0.0.0.0"
    port: int = Field(default=6055, ge=1, le=65535)
    websocket_path_prefix: str = "/ws"
    access_log: bool = False
    log_level: UvicornLogLevel = "info"

    @field_validator("websocket_path_prefix")
    @classmethod
    def normalize_websocket_path_prefix(cls, value: str) -> str:
        """规范化 NapCat WebSocket 路由前缀。"""
        cleaned_value = value.strip().rstrip("/")
        if cleaned_value == "":
            raise ValueError("WebSocket 路由前缀不能为空")
        if not cleaned_value.startswith("/"):
            cleaned_value = "/" + cleaned_value
        return cleaned_value


class NapCatConfig(ConfigModel):
    """NapCat 反向 WebSocket 连接配置。"""

    websocket_token: SecretStr | None = None
    send_max_attempts: int = Field(default=5, ge=1)
    send_retry_delay_seconds: float = Field(default=0, ge=0)

    @field_validator("websocket_token")
    @classmethod
    def validate_websocket_token(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        """空白 Token 表示不校验 NapCat WebSocket 请求。"""
        if value is not None and value.get_secret_value().strip() == "":
            return None
        return value


class ImageStorageConfig(ConfigModel):
    """群图片归档配置。"""

    directory: str = "images"
    download_concurrency: int = Field(default=16, ge=1)
    download_timeout_seconds: float = Field(default=20, gt=0)
    max_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    retry_delays_seconds: tuple[float, float, float] = (1, 5, 20)
    lease_seconds: float = Field(default=45, gt=0)

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: str) -> str:
        """确保图片归档目录不是空字符串。"""
        cleaned_value = value.strip()
        if cleaned_value == "":
            raise ValueError("图片归档目录不能为空")
        return cleaned_value

    @field_validator("retry_delays_seconds")
    @classmethod
    def validate_retry_delays(
        cls, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """确保三次图片重试延迟均为正数。"""
        if any(delay <= 0 for delay in value):
            raise ValueError("图片重试延迟必须全部大于 0")
        return value


class StorageConfig(ConfigModel):
    """文件存储配置。"""

    images: ImageStorageConfig = Field(default_factory=ImageStorageConfig)


class DatabaseConfig(ConfigModel):
    """PostgreSQL 连接池和超时配置。"""

    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "mybot"
    user: str = "mybot"
    password: SecretStr | None = None
    password_file: str | None = None
    pool_size: int = Field(default=20, ge=1)
    max_overflow: int = Field(default=20, ge=0)
    pool_timeout_seconds: float = Field(default=2, gt=0)
    statement_timeout_seconds: float = Field(default=5, gt=0)

    @field_validator("host", "name", "user")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """拒绝空白连接字段。"""
        cleaned_value = value.strip()
        if cleaned_value == "":
            raise ValueError("PostgreSQL 连接字段不能为空")
        return cleaned_value

    @field_validator("password_file")
    @classmethod
    def normalize_password_file(cls, value: str | None) -> str | None:
        """把空密码文件路径视为未配置。"""
        if value is None:
            return None
        cleaned_value = value.strip()
        return cleaned_value or None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr | None) -> SecretStr | None:
        """空白密码表示数据库不使用密码认证。"""
        if value is not None and value.get_secret_value().strip() == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_password_source(self) -> "DatabaseConfig":
        """配置认证信息时，内联密码和 secret 文件只能选择一种。"""
        has_password = self.password is not None
        has_password_file = self.password_file is not None
        if has_password and has_password_file:
            raise ValueError("database.password 与 password_file 不能同时配置")
        return self

    def resolve_password(self) -> str | None:
        """读取可选数据库密码，且不把密码写入日志。"""
        if self.password is not None:
            return self.password.get_secret_value()
        if self.password_file is None:
            return None
        password_path = Path(self.password_file)
        try:
            password = password_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"无法读取 PostgreSQL 密码文件: {password_path}") from exc
        return password or None

    def build_url(self) -> str:
        """使用 SQLAlchemy URL 统一转义连接字段和数据库密码。"""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.user,
            password=self.resolve_password(),
            host=self.host,
            port=self.port,
            database=self.name,
        ).render_as_string(hide_password=False)


class NetworkConfig(ConfigModel):
    """项目通用网络访问配置。"""

    proxy: str | None = None
    timeout_seconds: float = Field(default=15, gt=0)

    @field_validator("proxy")
    @classmethod
    def normalize_empty_proxy(cls, value: str | None) -> str | None:
        """把空代理字符串视为未配置代理。"""
        if value is None:
            return None
        cleaned_value = value.strip()
        return cleaned_value or None


class LoggingConfig(ConfigModel):
    """日志输出与归档策略配置。"""

    directory: str = "logs"
    console_level: LogLevelName = "INFO"
    file_level: LogLevelName = "DEBUG"
    rotation: str = "50 MB"
    retention: str = "30 days"
    compression: str = "gz"


class LLMProviderConfig(ConfigModel):
    """单个 OpenAI 兼容 LLM provider 配置。"""

    api_key: SecretStr | None = None
    base_url: str | None = None
    max_attempts: int = Field(default=5, ge=1)
    retry_delay_seconds: float = Field(default=0, ge=0)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        """空白 API key 表示上游服务不需要鉴权。"""
        if value is not None and value.get_secret_value().strip() == "":
            return None
        return value

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        """规范化可选服务地址。"""
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        return normalized or None


class LLMServiceConfig(ConfigModel):
    """具名 LLM provider 配置。"""

    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)

    @field_validator("providers")
    @classmethod
    def validate_provider_ids(
        cls, value: dict[str, LLMProviderConfig]
    ) -> dict[str, LLMProviderConfig]:
        """拒绝空白或带首尾空格的 provider ID。"""
        for provider_id in value:
            if provider_id.strip() == "" or provider_id != provider_id.strip():
                raise ValueError("LLM provider ID 不能为空或包含首尾空格")
        return value


class MCPServerConfig(ConfigModel):
    """单个 MCP stdio 服务配置。"""

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: str | None = None
    disabled: bool = False


class MCPConfig(ConfigModel):
    """MCP 总配置。"""

    enabled: bool = False
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class ModelRef(ConfigModel):
    """引用启动时已经注册的模型。"""

    provider: str
    name: str

    @field_validator("provider", "name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """拒绝空白模型引用。"""
        cleaned_value = value.strip()
        if cleaned_value == "":
            raise ValueError("模型 provider 和 name 不能为空")
        return cleaned_value


class ChatModelRef(ModelRef):
    """引用聊天模型及其图片输入能力。"""

    supports_images: bool = False


class AIGroupConfig(ConfigModel):
    """单个群的 AI 对话配置。"""

    id: NapCatId
    system_prompt_file: str
    knowledge_base_file: str | None = None
    max_context_tokens: int = Field(gt=0)

    @field_validator("system_prompt_file")
    @classmethod
    def validate_system_prompt_file(cls, value: str) -> str:
        """拒绝空系统提示词路径。"""
        cleaned_value = value.strip()
        if cleaned_value == "":
            raise ValueError("system_prompt_file 不能为空")
        return cleaned_value

    @field_validator("knowledge_base_file")
    @classmethod
    def normalize_knowledge_base_file(cls, value: str | None) -> str | None:
        """空知识库路径等同于未配置。"""
        if value is None:
            return None
        cleaned_value = value.strip()
        return cleaned_value or None


class AIVisionConfig(ConfigModel):
    """独立视觉描述工具配置。"""

    model: ModelRef
    system_prompt_file: str
    user_prompt_file: str
    max_attempts: int = Field(default=5, ge=1, le=10)
    retry_delay_seconds: float = Field(default=0.25, gt=0, le=10)
    retain_descriptions: bool = True


class AIImageConfig(ConfigModel):
    """AI 群聊图片读取和合并转发配置。"""

    max_per_turn: int = Field(default=20, ge=1, le=20)
    fetch_concurrency: int = Field(default=16, ge=1, le=32)
    download_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    forward_tool_enabled: bool = True
    forward_max_per_call: int = Field(default=20, ge=1, le=20)
    forward_max_per_turn: int = Field(default=50, ge=1, le=50)


class AIGroupChatConfig(ConfigModel):
    """AI 群聊插件配置。"""

    model: ChatModelRef
    vision: AIVisionConfig | None = None
    images: AIImageConfig = Field(default_factory=AIImageConfig)
    max_tool_rounds: int = Field(default=16, ge=1)
    token_safety_factor: float = Field(default=1.05, ge=1)
    context_compression_notice: str = "上下文有点长，我先整理一下记忆，稍等我几秒喵~"
    max_reply_chars: int = Field(default=1000, ge=1)
    show_reasoning: bool = False
    retain_reasoning: bool = False
    debug_dump_messages: bool = True
    extra_requirements_file: str = "ai_group_chat/prompts/extra_requirements.md"
    allow_mention_all: bool = False
    retain_tool_results: bool = False
    groups: tuple[AIGroupConfig, ...] = ()

    @model_validator(mode="after")
    def validate_vision_and_groups(self) -> "AIGroupChatConfig":
        """确保视觉配置与主模型能力一致，并拒绝重复群号。"""
        if self.model.supports_images and self.vision is not None:
            raise ValueError("主模型支持图片时不能配置 vision")
        if not self.model.supports_images and self.vision is None:
            raise ValueError("主模型不支持图片时必须配置 vision")
        group_ids = [item.id for item in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("AI 群聊 groups 不能包含重复群号")
        if self.extra_requirements_file.strip() == "":
            raise ValueError("extra_requirements_file 不能为空")
        return self


def _validate_unique_ids(values: tuple[NapCatId, ...], *, label: str) -> None:
    """拒绝重复 NapCat ID。"""
    if len(values) != len(set(values)):
        raise ValueError(f"{label} 不能包含重复 ID")


class GroupNoticeConfig(ConfigModel):
    """群成员变动提醒插件配置。"""

    groups: tuple[NapCatId, ...] = ()
    send_avatar: bool = True

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, value: tuple[NapCatId, ...]) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="group_notice.groups")
        return value


class AutoUnbanConfig(ConfigModel):
    """自动解禁插件配置。"""

    protected_users: tuple[NapCatId, ...] = ()

    @field_validator("protected_users")
    @classmethod
    def validate_users(
        cls, value: tuple[NapCatId, ...]
    ) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="auto_unban.protected_users")
        return value


class ImageGenerateConfig(ConfigModel):
    """OpenAI Images 生图插件配置。"""

    groups: tuple[NapCatId, ...] = ()
    model: ModelRef
    fetch_concurrency: int = Field(default=16, ge=1)
    download_timeout_seconds: float = Field(default=20.0, gt=0)

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, value: tuple[NapCatId, ...]) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="image_generate.groups")
        return value


class NeavoImageGenerateConfig(ConfigModel):
    """Neavo 群聊图像插件配置。"""

    groups: tuple[NapCatId, ...] = ()
    base_url: str
    api_token: SecretStr | None = None
    poll_interval_seconds: float = Field(ge=2.0, le=5.0, allow_inf_nan=False)
    generation_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    request_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_image_bytes: int = Field(gt=0)

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, value: tuple[NapCatId, ...]) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="neavo_image_generate.groups")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """校验并规范化 Neavo 服务根地址。"""
        base_url = value.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url 必须是有效的 HTTP 或 HTTPS 地址")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url 不允许包含查询参数或片段")
        return base_url

    @field_validator("api_token")
    @classmethod
    def validate_api_token(cls, value: SecretStr | None) -> SecretStr | None:
        """空白 Token 表示 Neavo 服务不需要 Bearer 鉴权。"""
        if value is None:
            return None
        token = value.get_secret_value().strip()
        if token == "":
            return None
        return SecretStr(token)


class EmptyPluginConfig(ConfigModel):
    """只通过配置节是否存在控制启停的插件配置。"""


class PluginsConfig(ConfigModel):
    """所有内置插件的可选配置。"""

    ai_group_chat: AIGroupChatConfig | None = None
    group_notice: GroupNoticeConfig | None = None
    auto_unban: AutoUnbanConfig | None = None
    image_generate: ImageGenerateConfig | None = None
    neavo_image_generate: NeavoImageGenerateConfig | None = None
    recall_bot_image: EmptyPluginConfig | None = None


class MyBotConfig(ConfigModel):
    """MyBot 唯一配置文件的完整模型。"""

    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    napcat: NapCatConfig
    storage: StorageConfig = Field(default_factory=StorageConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    llm: LLMServiceConfig = Field(default_factory=LLMServiceConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    database: DatabaseConfig
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
