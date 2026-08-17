"""插件自动发现入口。"""

import importlib.util
import sys
from operator import attrgetter
from pathlib import Path

from app.database import PluginMigrationSpec
from app.utils.log import log_event

from .base import PLUGINS, BasePlugin, Context

_INIT_FILE = "__init__.py"
_BASE_FILE = "base.py"
_PY_PATTERN = "*.py"
_MIGRATION_DIRECTORY = "migrations"


def load_all_plugins() -> None:
    """递归加载插件目录下的所有插件模块。"""
    current_dir = Path(__file__).parent
    skip_files = {_INIT_FILE, _BASE_FILE}

    for file_path in current_dir.rglob(_PY_PATTERN):
        if file_path.name in skip_files:
            continue
        if _MIGRATION_DIRECTORY in file_path.relative_to(current_dir).parts:
            continue

        module_name = f"{__name__}.{file_path.relative_to(current_dir).with_suffix('').as_posix().replace('/', '.')}"

        if module_name in sys.modules:
            continue

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            continue

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        log_event(
            level="INFO",
            event="plugin.loaded",
            category="plugin",
            message="插件模块加载成功",
            module_name=module_name,
        )

    PLUGINS.sort(key=attrgetter("priority"), reverse=True)


def discover_plugin_migrations() -> tuple[PluginMigrationSpec, ...]:
    """读取启用插件明确声明的 Alembic migration package。"""
    load_all_plugins()
    migrations: list[PluginMigrationSpec] = []
    for plugin in PLUGINS:
        package_name = plugin.migration_package
        if package_name is None:
            continue
        spec = importlib.util.find_spec(package_name)
        if spec is None or spec.submodule_search_locations is None:
            raise RuntimeError(f"插件 migration package 不存在: {package_name}")
        locations = tuple(spec.submodule_search_locations)
        if len(locations) != 1:
            raise RuntimeError(
                f"插件 migration package 必须对应单个目录: {package_name}"
            )
        migrations.append(
            PluginMigrationSpec(
                plugin_id=plugin.plugin_id,
                script_location=Path(locations[0]).resolve(),
            )
        )
    return tuple(sorted(migrations, key=lambda item: item.plugin_id))


__all__ = [
    "PLUGINS",
    "BasePlugin",
    "Context",
    "discover_plugin_migrations",
    "load_all_plugins",
]
