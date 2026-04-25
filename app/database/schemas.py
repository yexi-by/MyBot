"""数据库配置模型。"""

from pydantic import field_validator

from app.models.common import StrictModel


class RedisConfig(StrictModel):
    """Redis 连接配置。"""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None

    @field_validator("password")
    @classmethod
    def normalize_empty_password(cls, value: str | None) -> str | None:
        """把空 Redis 密码视为未设置密码。"""
        if value is None:
            return None
        cleaned_value = value.strip()
        if cleaned_value == "":
            return None
        return cleaned_value
