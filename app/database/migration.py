"""Core 和插件 Alembic migration 的显式运行与版本检查。"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from .models import CORE_SCHEMA, CORE_VERSION_TABLE
from .plugins import plugin_schema_name, validate_plugin_id


class DatabaseMigrationStateError(RuntimeError):
    """数据库实际 revision 与代码不一致。"""


@dataclass(frozen=True, slots=True)
class PluginMigrationSpec:
    """一个插件独立的 Alembic migration package。"""

    plugin_id: str
    script_location: Path

    def __post_init__(self) -> None:
        """校验插件身份和 migration 目录契约。"""
        _ = validate_plugin_id(self.plugin_id)
        if not self.script_location.is_dir():
            raise ValueError(f"migration 目录不存在: {self.script_location}")
        if not (self.script_location / "env.py").is_file():
            raise ValueError(f"migration 目录缺少 env.py: {self.script_location}")

    @property
    def schema(self) -> str:
        """返回插件独立 schema。"""
        return plugin_schema_name(self.plugin_id)


class PluginMigrationRegistry:
    """收集启用插件当前真实存在的 migration。"""

    def __init__(self) -> None:
        """初始化空 registry。"""
        self._specs: dict[str, PluginMigrationSpec] = {}

    def register(self, spec: PluginMigrationSpec) -> None:
        """按稳定 plugin_id 注册，重复身份直接报错。"""
        if spec.plugin_id in self._specs:
            raise ValueError(f"插件 migration 重复注册: {spec.plugin_id}")
        self._specs[spec.plugin_id] = spec

    def ordered_specs(self) -> tuple[PluginMigrationSpec, ...]:
        """按 plugin_id 稳定返回所有 migration。"""
        return tuple(self._specs[key] for key in sorted(self._specs))


class DatabaseMigrator:
    """先升级 core，再按 plugin_id 顺序升级插件。"""

    def __init__(
        self,
        *,
        database_url: str,
        plugin_registry: PluginMigrationRegistry | None = None,
        alembic_ini_path: Path | None = None,
    ) -> None:
        """保留连接与 migration 清单，不在应用启动时自动升级。"""
        if not database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url 必须使用 postgresql+asyncpg 驱动")
        self.database_url: str = database_url
        self.plugin_registry: PluginMigrationRegistry = (
            plugin_registry or PluginMigrationRegistry()
        )
        self.alembic_ini_path: Path = alembic_ini_path or (
            Path(__file__).resolve().parents[2] / "alembic.ini"
        )

    async def upgrade_all(self) -> None:
        """在独立线程中运行显式 migration，避免嵌套事件循环。"""
        await asyncio.to_thread(command.upgrade, self._core_config(), "head")
        for spec in self.plugin_registry.ordered_specs():
            await asyncio.to_thread(command.upgrade, self._plugin_config(spec), "head")

    async def assert_current(self) -> None:
        """检查 core 和插件版本，缺表或版本不符时拒绝启动。"""
        await self._assert_config_current(
            config=self._core_config(),
            schema=CORE_SCHEMA,
            version_table=CORE_VERSION_TABLE,
            label="core",
        )
        for spec in self.plugin_registry.ordered_specs():
            await self._assert_config_current(
                config=self._plugin_config(spec),
                schema=spec.schema,
                version_table=CORE_VERSION_TABLE,
                label=f"plugin:{spec.plugin_id}",
            )

    def _core_config(self) -> Config:
        """创建 core Alembic config。"""
        config = Config(str(self.alembic_ini_path))
        config.set_main_option("sqlalchemy.url", self._escaped_url())
        return config

    def _plugin_config(self, spec: PluginMigrationSpec) -> Config:
        """创建带独立 schema 和版本表的插件 config。"""
        config = Config(str(self.alembic_ini_path))
        config.set_main_option("script_location", str(spec.script_location))
        config.set_main_option("sqlalchemy.url", self._escaped_url())
        config.set_main_option("version_table", CORE_VERSION_TABLE)
        config.set_main_option("version_table_schema", spec.schema)
        config.attributes["plugin_id"] = spec.plugin_id
        config.attributes["plugin_schema"] = spec.schema
        return config

    def _escaped_url(self) -> str:
        """避免 ConfigParser 把 URL 中的百分号当成插值。"""
        return self.database_url.replace("%", "%%")

    async def _assert_config_current(
        self,
        *,
        config: Config,
        schema: str,
        version_table: str,
        label: str,
    ) -> None:
        """对比代码 head 与数据库版本表，不修改数据库。"""
        expected_heads = set(ScriptDirectory.from_config(config).get_heads())
        if len(expected_heads) != 1:
            raise DatabaseMigrationStateError(
                f"{label} migration 必须只有一个 head，实际为 {sorted(expected_heads)}"
            )
        engine = create_async_engine(self.database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                table_name = f"{schema}.{version_table}"
                exists = await connection.scalar(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": table_name},
                )
                if exists is None:
                    raise DatabaseMigrationStateError(
                        f"{label} migration 版本表不存在: {table_name}"
                    )
                actual_rows = await connection.scalars(
                    text(f'SELECT version_num FROM "{schema}"."{version_table}"')
                )
                actual_heads = set(actual_rows.all())
        finally:
            await engine.dispose()
        if actual_heads != expected_heads:
            raise DatabaseMigrationStateError(
                f"{label} migration 版本不匹配: "
                f"期望 {sorted(expected_heads)}，实际 {sorted(actual_heads)}"
            )
