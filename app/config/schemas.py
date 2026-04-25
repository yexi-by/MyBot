"""应用全局配置模型。"""

from typing import ClassVar

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.database.schemas import RedisConfig
from app.services import LLMConfig, LLMContextConfig, MCPConfig


class Settings(BaseSettings):
    """应用全局配置模型。"""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(extra="forbid")

    llm_settings: list[LLMConfig] = Field(default_factory=list)
    llm_context_config: LLMContextConfig | None = None
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    redis_config: RedisConfig
    video_and_image_path: str
    proxy: str | None = None
    password: str

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
