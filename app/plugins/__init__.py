import user_plugins
import pkgutil
import importlib
from .base import PLUGINS


def load_all_plugins():
    for finder, name, ispkg in pkgutil.iter_modules(user_plugins.__path__):
        importlib.import_module(f"{user_plugins.__name__}.{name}")


load_all_plugins()

__all__ = ["PLUGINS"]
