"""MyBot core schema 的 Alembic 异步运行环境。"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database.models import CORE_SCHEMA, CORE_VERSION_TABLE, DatabaseBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = DatabaseBase.metadata


def do_run_migrations(connection: Connection) -> None:
    """在 Alembic 同步连接适配器上执行 migration。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        compare_type=True,
        version_table=CORE_VERSION_TABLE,
        version_table_schema=CORE_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """创建 asyncpg engine，确保版本表 schema 存在后执行 migration。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        _ = await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{CORE_SCHEMA}"'))
        await connection.commit()
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    raise RuntimeError("MyBot migration 必须连接真实 PostgreSQL 执行")

asyncio.run(run_async_migrations())
