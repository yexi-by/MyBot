import importlib.util
import sys
from operator import attrgetter
from pathlib import Path

from app.utils import logger

from .base import PLUGINS, BasePlugin, Context

_INIT_FILE = "__init__.py"
_BASE_FILE = "base.py"
_PY_PATTERN = "*.py"


def load_all_plugins() -> None:
    """递归加载插件目录下的所有插件模块。"""
    current_dir = Path(__file__).parent
    skip_files = {_INIT_FILE, _BASE_FILE}

    for file_path in current_dir.rglob(_PY_PATTERN):
        if file_path.name in skip_files:
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
        logger.info(f"[加载成功] {module_name}")


load_all_plugins()
PLUGINS.sort(key=attrgetter("priority"), reverse=True)

__all__ = ["PLUGINS", "BasePlugin", "Context"]
