"""核心 Alembic migration 的显式运行与版本检查。"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from .models import CORE_SCHEMA, CORE_VERSION_TABLE


class DatabaseMigrationStateError(RuntimeError):
    """数据库实际 revision 与代码不一致。"""


class DatabaseMigrator:
    """显式升级核心数据库，并在启动时核对版本。"""

    def __init__(
        self,
        *,
        database_url: str,
        alembic_ini_path: Path | None = None,
    ) -> None:
        """保存数据库连接和 Alembic 配置路径。"""
        if not database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url 必须使用 postgresql+asyncpg 驱动")
        self.database_url: str = database_url
        self.alembic_ini_path: Path = alembic_ini_path or (
            Path(__file__).resolve().parents[2] / "alembic.ini"
        )

    async def upgrade_all(self) -> None:
        """在独立线程中运行显式 migration，避免嵌套事件循环。"""
        await asyncio.to_thread(command.upgrade, self._core_config(), "head")

    async def assert_current(self) -> None:
        """检查 core 版本，缺表或版本不符时拒绝启动。"""
        expected_heads = set(ScriptDirectory.from_config(self._core_config()).get_heads())
        if len(expected_heads) != 1:
            raise DatabaseMigrationStateError(
                f"core migration 必须只有一个 head，实际为 {sorted(expected_heads)}"
            )
        engine = create_async_engine(self.database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                table_name = f"{CORE_SCHEMA}.{CORE_VERSION_TABLE}"
                exists = await connection.scalar(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": table_name},
                )
                if exists is None:
                    raise DatabaseMigrationStateError(
                        f"core migration 版本表不存在: {table_name}"
                    )
                actual_rows = await connection.scalars(
                    text(f'SELECT version_num FROM "{CORE_SCHEMA}"."{CORE_VERSION_TABLE}"')
                )
                actual_heads = set(actual_rows.all())
        finally:
            await engine.dispose()
        if actual_heads != expected_heads:
            raise DatabaseMigrationStateError(
                "core migration 版本不匹配: "
                f"期望 {sorted(expected_heads)}，实际 {sorted(actual_heads)}"
            )

    def _core_config(self) -> Config:
        """创建 core Alembic 配置，避免 URL 中的百分号被当作插值。"""
        config = Config(str(self.alembic_ini_path))
        config.set_main_option("sqlalchemy.url", self.database_url.replace("%", "%%"))
        return config
