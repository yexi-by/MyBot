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
type ImageDetail = Literal["omit", "auto", "low", "high"]
type ImageOversizeBehavior = Literal["error", "skip", "describe"]
type ImageDeliveryMode = Literal["direct", "vision"]
type ToolResultRetention = Literal["off", "summary", "full"]


class ConfigModel(StrictModel):
    """不可原地修改的配置模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class AppConfig(ConfigModel):
    """应用自身元信息配置。"""

    name: str
    environment: AppEnvironment


class ServerConfig(ConfigModel):
    """HTTP 与 WebSocket 服务监听配置。"""

    host: str
    port: int = Field(ge=1, le=65535)
    websocket_path_prefix: str
    access_log: bool
    log_level: UvicornLogLevel
    config_watch_debounce_ms: int = Field(ge=0)
    config_watch_step_ms: int = Field(ge=1)
    config_watcher_stop_timeout_seconds: float = Field(gt=0)
    power_action_delay_seconds: float = Field(ge=0)

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
    action_timeout_seconds: float = Field(gt=0)
    send_max_attempts: int = Field(ge=1)
    send_retry_delay_seconds: float = Field(ge=0)
    send_retry_max_delay_seconds: float = Field(ge=0)
    response_summary_max_chars: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_retry_delays(self) -> "NapCatConfig":
        """最大退避不能小于初始退避。"""
        if (
            self.send_retry_max_delay_seconds > 0
            and self.send_retry_max_delay_seconds < self.send_retry_delay_seconds
        ):
            raise ValueError("send_retry_max_delay_seconds 不能小于初始重试延迟")
        return self

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

    directory: str
    download_concurrency: int = Field(ge=1)
    download_timeout_seconds: float = Field(gt=0)
    max_bytes: int = Field(ge=1)
    retry_delays_seconds: tuple[float, ...]
    lease_seconds: float = Field(gt=0)
    worker_poll_interval_seconds: float = Field(gt=0)
    worker_stop_timeout_seconds: float = Field(gt=0)

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
        cls, value: tuple[float, ...]
    ) -> tuple[float, ...]:
        """确保图片重试延迟均为非负数；空列表表示不重试。"""
        if any(delay < 0 for delay in value):
            raise ValueError("图片重试延迟不能小于 0")
        return value


class StorageConfig(ConfigModel):
    """文件存储配置。"""

    images: ImageStorageConfig


class DatabaseConfig(ConfigModel):
    """PostgreSQL 连接池和超时配置。"""

    host: str
    port: int = Field(ge=1, le=65535)
    name: str
    user: str
    password: SecretStr | None = None
    password_file: str | None = None
    pool_size: int = Field(ge=1)
    max_overflow: int = Field(ge=0)
    pool_timeout_seconds: float = Field(gt=0)
    statement_timeout_seconds: float = Field(ge=0)
    persistence_retry_delays_seconds: tuple[float, ...]

    @field_validator("persistence_retry_delays_seconds")
    @classmethod
    def validate_persistence_retry_delays(
        cls, value: tuple[float, ...]
    ) -> tuple[float, ...]:
        """允许用空列表关闭重试，并拒绝负延迟。"""
        if any(delay < 0 for delay in value):
            raise ValueError("PostgreSQL 持久化重试延迟不能小于 0")
        return value

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
    timeout_seconds: float = Field(gt=0)

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

    directory: str
    console_level: LogLevelName
    file_level: LogLevelName
    rotation: str
    retention: str
    compression: str


class LLMProviderConfig(ConfigModel):
    """单个 OpenAI 兼容 LLM provider 配置。"""

    api_key: SecretStr | None = None
    base_url: str | None = None
    proxy: str | None = None
    inherit_network_proxy: bool
    timeout_seconds: float = Field(ge=0)
    max_attempts: int = Field(ge=1)
    retry_delay_seconds: float = Field(ge=0)
    retry_max_delay_seconds: float = Field(ge=0)

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

    @field_validator("proxy")
    @classmethod
    def normalize_proxy(cls, value: str | None) -> str | None:
        """空代理表示继承全局网络配置。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_retry_delays(self) -> "LLMProviderConfig":
        """最大退避不能小于初始退避。"""
        if (
            self.retry_max_delay_seconds > 0
            and self.retry_max_delay_seconds < self.retry_delay_seconds
        ):
            raise ValueError("retry_max_delay_seconds 不能小于初始重试延迟")
        return self


class LLMServiceConfig(ConfigModel):
    """具名 LLM provider 配置。"""

    providers: dict[str, LLMProviderConfig]

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
    args: tuple[str, ...]
    env: dict[str, str] | None = None
    cwd: str | None = None
    disabled: bool


class MCPConfig(ConfigModel):
    """MCP 总配置。"""

    enabled: bool
    servers: dict[str, MCPServerConfig]


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

    supports_images: bool


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
    max_attempts: int = Field(ge=1)
    retry_delay_seconds: float = Field(ge=0)
    retry_max_delay_seconds: float = Field(ge=0)
    retain_descriptions: bool

    @model_validator(mode="after")
    def validate_retry_delays(self) -> "AIVisionConfig":
        """最大退避不能小于初始退避。"""
        if (
            self.retry_max_delay_seconds > 0
            and self.retry_max_delay_seconds < self.retry_delay_seconds
        ):
            raise ValueError("retry_max_delay_seconds 不能小于初始重试延迟")
        return self


class AIImageConfig(ConfigModel):
    """AI 群聊图片读取和合并转发配置。"""

    max_per_turn: int = Field(ge=0)
    fetch_concurrency: int = Field(ge=1)
    download_timeout_seconds: float = Field(gt=0)
    max_image_bytes: int = Field(ge=0)
    max_total_bytes_per_request: int = Field(ge=0)
    max_width: int = Field(ge=0)
    max_height: int = Field(ge=0)
    allowed_mime_types: tuple[str, ...]
    delivery_mode: ImageDeliveryMode
    oversize_behavior: ImageOversizeBehavior
    image_detail: ImageDetail
    retain_images: bool
    forward_tool_enabled: bool
    forward_max_per_call: int = Field(ge=0)
    forward_max_per_turn: int = Field(ge=0)

    @field_validator("allowed_mime_types")
    @classmethod
    def normalize_allowed_mime_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """规范化用户声明的 MIME 类型并拒绝空项。"""
        normalized = tuple(item.strip().lower() for item in value)
        if any(item == "" for item in normalized):
            raise ValueError("allowed_mime_types 不能包含空值")
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed_mime_types 不能包含重复值")
        return normalized


class AIMessageFormattingConfig(ConfigModel):
    """模型可读消息文本化策略；0 表示不在格式化阶段截断。"""

    field_text_limit: int = Field(ge=0)
    json_text_limit: int = Field(ge=0)
    markdown_text_limit: int = Field(ge=0)
    forward_max_items: int = Field(ge=0)
    forward_max_depth: int = Field(ge=-1)
    nested_text_search_max_depth: int = Field(ge=-1)


class AIHistoryConfig(ConfigModel):
    """群历史工具的默认分页大小和可选单页上限。"""

    default_limit: int = Field(ge=1)
    max_per_call: int = Field(ge=0)
    default_before_count: int = Field(ge=0)
    default_after_count: int = Field(ge=0)


class AIFileToolConfig(ConfigModel):
    """群文件工具的默认返回数量和可选单次上限。"""

    default_count: int = Field(ge=1)
    max_per_call: int = Field(ge=0)


class AITokenEstimatorConfig(ConfigModel):
    """无 tokenizer 时使用的可调 token 估算参数。"""

    request_overhead_tokens: int = Field(ge=0)
    message_overhead_tokens: int = Field(ge=0)
    tool_call_overhead_tokens: int = Field(ge=0)
    image_tokens: int = Field(ge=0)
    ascii_tokens_per_character: float = Field(ge=0, allow_inf_nan=False)
    non_ascii_tokens_per_character: float = Field(ge=0, allow_inf_nan=False)


class AIGroupChatConfig(ConfigModel):
    """AI 群聊插件配置。"""

    model: ChatModelRef
    vision: AIVisionConfig | None = None
    images: AIImageConfig
    formatting: AIMessageFormattingConfig
    history: AIHistoryConfig
    files: AIFileToolConfig
    token_estimator: AITokenEstimatorConfig
    max_tool_rounds: int = Field(ge=1)
    token_safety_factor: float = Field(gt=0, allow_inf_nan=False)
    context_compression_notice: str
    forward_reply_threshold_chars: int = Field(ge=0)
    show_reasoning: bool
    retain_reasoning: bool
    debug_dump_messages: bool
    debug_dump_directory: str
    extra_requirements_file: str
    allow_mention_all: bool
    tool_result_retention: ToolResultRetention
    groups: tuple[AIGroupConfig, ...]

    @model_validator(mode="after")
    def validate_vision_and_groups(self) -> "AIGroupChatConfig":
        """确保视觉配置与主模型能力一致，并拒绝重复群号。"""
        delivery_mode = self.images.delivery_mode
        oversize_behavior = self.images.oversize_behavior
        if delivery_mode == "direct" and not self.model.supports_images:
            raise ValueError("主模型不支持图片时 images.delivery_mode 不能为 direct")
        if delivery_mode == "vision" and self.vision is None:
            raise ValueError("images.delivery_mode 为 vision 时必须配置 vision")
        if oversize_behavior == "describe":
            if delivery_mode != "direct":
                raise ValueError("oversize_behavior=describe 只用于 direct 模式")
            if self.vision is None:
                raise ValueError("oversize_behavior=describe 时必须配置 vision")
        if (
            self.vision is not None
            and delivery_mode != "vision"
            and oversize_behavior != "describe"
        ):
            raise ValueError("当前图片策略不会使用 vision，请删除该配置或调整图片策略")
        if self.images.retain_images and delivery_mode != "direct":
            raise ValueError("retain_images 只适用于 direct 模式")
        group_ids = [item.id for item in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("AI 群聊 groups 不能包含重复群号")
        if self.extra_requirements_file.strip() == "":
            raise ValueError("extra_requirements_file 不能为空")
        if self.debug_dump_directory.strip() == "":
            raise ValueError("debug_dump_directory 不能为空")
        return self


def _validate_unique_ids(values: tuple[NapCatId, ...], *, label: str) -> None:
    """拒绝重复 NapCat ID。"""
    if len(values) != len(set(values)):
        raise ValueError(f"{label} 不能包含重复 ID")


class GroupNoticeConfig(ConfigModel):
    """群成员变动提醒插件配置。"""

    groups: tuple[NapCatId, ...]
    send_avatar: bool

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, value: tuple[NapCatId, ...]) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="group_notice.groups")
        return value


class AutoUnbanConfig(ConfigModel):
    """自动解禁插件配置。"""

    protected_users: tuple[NapCatId, ...]

    @field_validator("protected_users")
    @classmethod
    def validate_users(
        cls, value: tuple[NapCatId, ...]
    ) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="auto_unban.protected_users")
        return value


class ImageGenerateConfig(ConfigModel):
    """OpenAI Images 生图插件配置。"""

    groups: tuple[NapCatId, ...]
    model: ModelRef
    fetch_concurrency: int = Field(ge=1)
    download_timeout_seconds: float = Field(gt=0)
    max_input_image_bytes: int = Field(ge=0)
    command: str
    help_command: str

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, value: tuple[NapCatId, ...]) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="image_generate.groups")
        return value

    @field_validator("command", "help_command")
    @classmethod
    def validate_commands(cls, value: str) -> str:
        """拒绝空白命令。"""
        command = value.strip()
        if command == "":
            raise ValueError("生图命令不能为空")
        return command

    @model_validator(mode="after")
    def validate_distinct_commands(self) -> "ImageGenerateConfig":
        """执行命令和帮助命令不能相同。"""
        if self.command == self.help_command:
            raise ValueError("command 与 help_command 不能相同")
        return self


class NeavoImageGenerateConfig(ConfigModel):
    """Neavo 群聊图像插件配置。"""

    groups: tuple[NapCatId, ...]
    base_url: str
    api_token: SecretStr | None = None
    poll_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    generation_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    request_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_prompt_chars: int = Field(ge=0)
    max_input_image_bytes: int = Field(ge=0)
    max_output_image_bytes: int = Field(ge=0)
    allowed_input_mime_types: tuple[str, ...]
    max_consecutive_poll_errors: int = Field(ge=0)
    generate_command: str
    describe_command: str

    @field_validator("allowed_input_mime_types")
    @classmethod
    def normalize_allowed_input_mime_types(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        """规范化可选输入 MIME 白名单；空列表表示不限制。"""
        normalized = tuple(item.strip().lower() for item in value)
        if any(item == "" for item in normalized):
            raise ValueError("allowed_input_mime_types 不能包含空值")
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed_input_mime_types 不能包含重复值")
        return normalized

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, value: tuple[NapCatId, ...]) -> tuple[NapCatId, ...]:
        _validate_unique_ids(value, label="neavo_image_generate.groups")
        return value

    @field_validator("generate_command", "describe_command")
    @classmethod
    def validate_commands(cls, value: str) -> str:
        """拒绝空白命令。"""
        command = value.strip()
        if command == "":
            raise ValueError("Neavo 命令不能为空")
        return command

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

    @model_validator(mode="after")
    def validate_distinct_commands(self) -> "NeavoImageGenerateConfig":
        """两种 Neavo 操作必须使用不同命令。"""
        if self.generate_command == self.describe_command:
            raise ValueError("generate_command 与 describe_command 不能相同")
        return self


class RecallBotImageConfig(ConfigModel):
    """机器人图片撤回插件配置。"""

    command: str
    failure_detail_max_chars: int = Field(ge=0)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        """拒绝空白撤回命令。"""
        command = value.strip()
        if command == "":
            raise ValueError("撤回命令不能为空")
        return command


class PluginRuntimeConfig(ConfigModel):
    """单个插件的消费者数量与事件路由优先级。"""

    consumers_count: int = Field(ge=1)
    priority: int


class PluginExecutionConfig(ConfigModel):
    """需要在进程启动时应用的插件执行参数。"""

    stop_timeout_seconds: float = Field(gt=0)
    plugins: dict[str, PluginRuntimeConfig]

    @field_validator("plugins")
    @classmethod
    def validate_plugin_ids(
        cls, value: dict[str, PluginRuntimeConfig]
    ) -> dict[str, PluginRuntimeConfig]:
        """配置键必须是可按字面匹配的非空插件 ID。"""
        for plugin_id in value:
            if plugin_id.strip() == "" or plugin_id != plugin_id.strip():
                raise ValueError("plugin_execution.plugins 的插件 ID 不能为空或包含首尾空格")
        return value

    def for_plugin(self, plugin_id: str) -> PluginRuntimeConfig:
        """按稳定插件 ID 返回启动期执行参数。"""
        try:
            return self.plugins[plugin_id]
        except KeyError as exc:
            raise KeyError(f"插件没有执行配置: {plugin_id}") from exc


class PluginsConfig(ConfigModel):
    """所有内置插件的可选配置。"""

    ai_group_chat: AIGroupChatConfig | None = None
    group_notice: GroupNoticeConfig | None = None
    auto_unban: AutoUnbanConfig | None = None
    image_generate: ImageGenerateConfig | None = None
    neavo_image_generate: NeavoImageGenerateConfig | None = None
    recall_bot_image: RecallBotImageConfig | None = None


class MyBotConfig(ConfigModel):
    """MyBot 唯一配置文件的完整模型。"""

    app: AppConfig
    server: ServerConfig
    napcat: NapCatConfig
    storage: StorageConfig
    network: NetworkConfig
    logging: LoggingConfig
    llm: LLMServiceConfig
    mcp: MCPConfig
    database: DatabaseConfig
    plugin_execution: PluginExecutionConfig
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
