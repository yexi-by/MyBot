"""插件 Alembic package 共用的异步运行环境。"""

import asyncio

from alembic import context
from sqlalchemy import Connection, MetaData, pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from .models import CORE_VERSION_TABLE
from .plugins import PLUGIN_SCHEMA_TOKEN, plugin_schema_name, validate_plugin_id


def _do_run_migrations(
    connection: Connection,
    *,
    target_metadata: MetaData | None,
    schema: str,
    version_table: str,
) -> None:
    """在已绑定插件 schema 的同步连接适配器上运行 migration。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        compare_type=True,
        version_table=version_table,
        version_table_schema=schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_plugin_migration_environment(
    *, target_metadata: MetaData | None = None
) -> None:
    """从 DatabaseMigrator 注入的身份创建 schema 并执行插件 migration。"""
    if context.is_offline_mode():
        raise RuntimeError("插件 migration 必须连接真实 PostgreSQL 执行")
    config = context.config
    raw_plugin_id = config.attributes.get("plugin_id")
    raw_schema = config.attributes.get("plugin_schema")
    if not isinstance(raw_plugin_id, str):
        raise RuntimeError("插件 migration 缺少 plugin_id")
    if not isinstance(raw_schema, str):
        raise RuntimeError("插件 migration 缺少 plugin_schema")
    plugin_id = validate_plugin_id(raw_plugin_id)
    expected_schema = plugin_schema_name(plugin_id)
    if raw_schema != expected_schema:
        raise RuntimeError(
            f"插件 migration schema 与 plugin_id 不匹配: {raw_schema}"
        )
    version_table = config.get_main_option("version_table") or CORE_VERSION_TABLE

    async def run_async_migrations() -> None:
        """使用 NullPool 执行单次插件 migration。"""
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        async with connectable.connect() as connection:
            _ = await connection.execute(
                text(f'CREATE SCHEMA IF NOT EXISTS "{expected_schema}"')
            )
            await connection.commit()
            translated_connection = await connection.execution_options(
                schema_translate_map={PLUGIN_SCHEMA_TOKEN: expected_schema}
            )

            def run_sync(sync_connection: Connection) -> None:
                """把同步 Alembic API 局限在 run_sync 边界。"""
                _do_run_migrations(
                    sync_connection,
                    target_metadata=target_metadata,
                    schema=expected_schema,
                    version_table=version_table,
                )

            await translated_connection.run_sync(run_sync)
        await connectable.dispose()

    asyncio.run(run_async_migrations())
