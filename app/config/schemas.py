"""应用全局配置模型。"""

from typing import ClassVar, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.database.schemas import RedisConfig
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


class StorageConfig(StrictModel):
    """本地运行数据与媒体缓存配置。"""

    media_path: str = "data"

    @field_validator("media_path")
    @classmethod
    def validate_media_path(cls, value: str) -> str:
        """确保媒体缓存目录不是空字符串。"""
        cleaned_value = value.strip()
        if cleaned_value == "":
            raise ValueError("媒体缓存目录不能为空")
        return cleaned_value


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
    """日志输出与保留策略配置。"""

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
    redis: RedisConfig
