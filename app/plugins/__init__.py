import user_plugins
import pkgutil
import importlib
from operator import attrgetter
from .base import PLUGINS,PluginContext,BasePlugin


def load_all_plugins():
    for finder, name, ispkg in pkgutil.iter_modules(user_plugins.__path__):
        importlib.import_module(f"{user_plugins.__name__}.{name}")


load_all_plugins()

PLUGINS.sort(key=attrgetter("priority"), reverse=True)

__all__ = ["PLUGINS","PluginContext","BasePlugin"]
