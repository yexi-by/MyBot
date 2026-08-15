"""PostgreSQL 异步连接池和 session 生命周期。"""

from dataclasses import dataclass
from math import ceil

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True, slots=True)
class PostgreSQLRuntime:
    """仅在基础设施层持有 engine 和 session factory。"""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def create(
        cls,
        *,
        database_url: str,
        pool_size: int = 10,
        max_overflow: int = 10,
        pool_timeout_seconds: float = 5.0,
        statement_timeout_seconds: float = 5.0,
    ) -> "PostgreSQLRuntime":
        """按系统持久性和连接池约束创建运行时。"""
        if not database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url 必须使用 postgresql+asyncpg 驱动")
        if pool_size < 1:
            raise ValueError("pool_size 必须大于等于 1")
        if max_overflow < 0:
            raise ValueError("max_overflow 不能小于 0")
        if pool_timeout_seconds <= 0:
            raise ValueError("pool_timeout_seconds 必须大于 0")
        if statement_timeout_seconds <= 0:
            raise ValueError("statement_timeout_seconds 必须大于 0")
        statement_timeout_ms = max(1, ceil(statement_timeout_seconds * 1000))
        engine = create_async_engine(
            database_url,
            isolation_level="READ COMMITTED",
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_seconds,
            connect_args={
                "server_settings": {
                    "synchronous_commit": "on",
                    "statement_timeout": str(statement_timeout_ms),
                }
            },
        )
        return cls(
            engine=engine,
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
        )

    async def check_connection(self) -> None:
        """确认 PostgreSQL 可连接，否则让启动直接失败。"""
        async with self.engine.connect() as connection:
            _ = await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """关闭连接池中的所有连接。"""
        await self.engine.dispose()
