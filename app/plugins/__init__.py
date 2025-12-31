import importlib
import pkgutil
from operator import attrgetter
from . import user_plugins
from .base import PLUGINS, BasePlugin, PluginContext


def load_all_plugins():
    prefix = user_plugins.__name__ + "."
    for finder, name, ispkg in pkgutil.walk_packages(user_plugins.__path__, prefix):
        importlib.import_module(name)


load_all_plugins()

PLUGINS.sort(key=attrgetter("priority"), reverse=True)

__all__ = ["PLUGINS", "PluginContext", "BasePlugin",]
