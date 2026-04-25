"""数据库配置模型。"""

from app.models.common import StrictModel


class RedisConfig(StrictModel):
    """Redis 连接配置。"""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None
