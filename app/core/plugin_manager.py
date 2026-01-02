import inspect
from collections import defaultdict
from types import UnionType
from typing import Any, Callable, Union, get_args, get_origin

from app.models import AllEvent
from app.plugins import BasePlugin


class PluginController:
    def __init__(
        self,
        plugin_objects: list[BasePlugin],
    ) -> None:
        self.plugin_objects = plugin_objects
        self.handlers_map: dict[type[AllEvent], list[tuple[Callable, str]]] = (
            defaultdict(list)
        )
        self._load_plugins()

    @staticmethod
    def get_dependency(func: Callable[..., Any]) -> dict[str, type[AllEvent]]:
        sig = inspect.signature(func)
        valid_params = [p for p in sig.parameters.values() if p.name != "self"]
        if len(valid_params) != 1:
            raise ValueError(
                f"插件定义错误: 方法 '{func.__name__}' 必须且只能接受 1 个事件参数。"
            )
        dependencies = {}
        param = valid_params[0]
        if param.annotation is inspect.Parameter.empty:
            raise ValueError(f"错误: 参数 '{param.name}' 缺少类型注解")
        dependencies[param.name] = param.annotation
        return dependencies

    def _load_plugins(self) -> None:
        for plugin in self.plugin_objects:
            dependencies = self.get_dependency(plugin.run)
            param_name, event_type = next(iter(dependencies.items()))
            origin = get_origin(event_type)
            if origin is Union or origin is UnionType:
                args = get_args(event_type)
                for arg in args:
                    self.handlers_map[arg].append((plugin.add_to_queue, param_name))
            else:
                self.handlers_map[event_type].append((plugin.add_to_queue, param_name))
