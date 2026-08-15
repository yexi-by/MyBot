"""应用全局配置模型。"""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from app.models import StrictModel
from app.services import LLMConfig, MCPConfig

type AppEnvironment = Literal["development", "staging", "production", "test"]
type UvicornLogLevel = Literal["critical", "error", "warning", "info", "debug", "trace"]
type LogLevelName = Literal[
    "TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"
]


class AppConfig(StrictModel):
    """应用自身元信息配置。"""

    name: str = "MyBot"
    environment: AppEnvironment = "production"


class ServerConfig(StrictModel):
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


class NapCatConfig(StrictModel):
    """NapCat 反向 WebSocket 连接配置。"""

    websocket_token: str = Field(min_length=1)
    send_retry_count: int = Field(default=3, ge=1)
    send_retry_delay: int = Field(default=1, ge=0)


class StorageConfig(StrictModel):
    """群图片归档配置。"""

    image_path: str = "images"
    image_download_concurrency: int = Field(default=8, ge=1)
    image_download_timeout_seconds: float = Field(default=30, gt=0)
    image_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    image_retry_delays_seconds: tuple[float, float, float] = (5, 30, 300)
    image_lease_seconds: float = Field(default=60, gt=0)

    @field_validator("image_path")
    @classmethod
    def validate_image_path(cls, value: str) -> str:
        """确保图片归档目录不是空字符串。"""
        cleaned_value = value.strip()
        if cleaned_value == "":
            raise ValueError("图片归档目录不能为空")
        return cleaned_value

    @field_validator("image_retry_delays_seconds")
    @classmethod
    def validate_image_retry_delays(
        cls, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """确保三次图片重试延迟均为正数。"""
        if any(delay <= 0 for delay in value):
            raise ValueError("图片重试延迟必须全部大于 0")
        return value


class DatabaseConfig(StrictModel):
    """PostgreSQL 连接池和超时配置。"""

    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "mybot"
    user: str = "mybot"
    password: SecretStr | None = None
    password_file: str | None = None
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout_seconds: float = Field(default=5, gt=0)
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
        """拒绝空白密码，合法密码保持原值且不写入错误信息。"""
        if value is not None and value.get_secret_value().strip() == "":
            raise ValueError("PostgreSQL 密码不能为空")
        return value

    @model_validator(mode="after")
    def validate_password_source(self) -> "DatabaseConfig":
        """确保密码只来自配置值或 secret 文件之一。"""
        has_password = self.password is not None
        has_password_file = self.password_file is not None
        if has_password == has_password_file:
            raise ValueError("database.password 与 password_file 必须且只能配置一个")
        return self

    def resolve_password(self) -> str:
        """读取数据库密码，且不把密码写入日志。"""
        if self.password is not None:
            return self.password.get_secret_value()
        if self.password_file is None:
            raise RuntimeError("PostgreSQL 密码来源未配置")
        password_path = Path(self.password_file)
        try:
            password = password_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"无法读取 PostgreSQL 密码文件: {password_path}") from exc
        if password == "":
            raise RuntimeError("PostgreSQL 密码文件不能为空")
        return password

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


class NetworkConfig(StrictModel):
    """项目通用网络访问配置。"""

    proxy: str | None = None
    timeout_seconds: float = Field(default=30, gt=0)

    @field_validator("proxy")
    @classmethod
    def normalize_empty_proxy(cls, value: str | None) -> str | None:
        """把空代理字符串视为未配置代理。"""
        if value is None:
            return None
        cleaned_value = value.strip()
        if cleaned_value == "":
            return None
        return cleaned_value


class LoggingConfig(StrictModel):
    """日志输出与归档策略配置。"""

    directory: str = "logs"
    console_level: LogLevelName = "INFO"
    file_level: LogLevelName = "DEBUG"
    rotation: str = "50 MB"
    retention: str = "30 days"
    compression: str = "gz"


class LLMServiceConfig(StrictModel):
    """LLM 服务总配置。"""

    providers: list[LLMConfig] = Field(default_factory=list)


class Settings(BaseSettings):
    """应用全局配置模型。"""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(extra="forbid")

    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    napcat: NapCatConfig
    storage: StorageConfig = Field(default_factory=StorageConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    llm: LLMServiceConfig = Field(default_factory=LLMServiceConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    database: DatabaseConfig
