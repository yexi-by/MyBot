"""`python -m app.database.migrations` 显式 migration 命令。"""

import argparse
import asyncio
import sys

from app.database.migration import DatabaseMigrator, PluginMigrationRegistry


async def _run(
    command_name: str,
    plugin_registry: PluginMigrationRegistry | None = None,
) -> None:
    """读取本机配置并执行升级或版本检查。"""
    from app.config import ConfigManager
    from app.plugins import discover_plugin_migrations

    config = ConfigManager.create().boot_config
    actual_registry = plugin_registry
    if actual_registry is None:
        actual_registry = PluginMigrationRegistry()
        for spec in discover_plugin_migrations():
            actual_registry.register(spec)
    migrator = DatabaseMigrator(
        database_url=config.database.build_url(),
        plugin_registry=actual_registry,
    )
    if command_name == "upgrade":
        await migrator.upgrade_all()
        await migrator.assert_current()
        return
    await migrator.assert_current()


def main() -> int:
    """解析命令且避免将数据库密码写入错误输出。"""
    parser = argparse.ArgumentParser(description="MyBot PostgreSQL migration")
    _ = parser.add_argument("command", choices=("upgrade", "check"))
    args = parser.parse_args()
    command_name = str(args.command)
    try:
        asyncio.run(_run(command_name))
    except Exception as exc:
        # 连接异常可能携带 DSN；命令只输出异常类型和固定说明。
        print(
            f"{type(exc).__name__}: PostgreSQL migration {command_name} 失败",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
