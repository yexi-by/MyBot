"""插件事件路由。"""

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast, get_args, get_type_hints

from app.models import AllEvent

if TYPE_CHECKING:
    from app.plugins.base import BasePlugin

type EventHandler = Callable[..., Awaitable[bool]]


class PluginController:
    """管理插件实例并建立 NapCat 事件路由。"""

    def __init__(
        self,
        plugin_objects: list["BasePlugin[AllEvent]"],
    ) -> None:
        """保存插件实例并构建事件路由。"""
        self.plugin_objects: list["BasePlugin[AllEvent]"] = plugin_objects
        self.handlers_map: dict[
            type[AllEvent], list[tuple[EventHandler, str]]
        ] = defaultdict(list)
        self._load_plugins()

    @staticmethod
    def _get_event_parameter(func: EventHandler) -> tuple[str, object]:
        """读取插件 run 方法的事件参数及类型注解。"""
        sig = inspect.signature(func)
        valid_params = [p for p in sig.parameters.values() if p.name != "self"]
        if len(valid_params) != 1:
            raise ValueError(
                f"插件定义错误: 方法 '{func.__name__}' 必须且只能接受 1 个事件参数。"
            )
        param = valid_params[0]
        hints = get_type_hints(func)
        annotation: object | None = hints.get(param.name)
        if annotation is None:
            raise ValueError(f"错误: 参数 '{param.name}' 缺少类型注解")
        return param.name, annotation

    @staticmethod
    def _resolve_event_types(annotation: object) -> tuple[type[AllEvent], ...]:
        """把单个事件类型或联合事件类型收窄为事件类型元组。"""
        raw_types = get_args(annotation)
        if not raw_types:
            raw_types = (annotation,)
        event_types: list[type[AllEvent]] = []
        for raw_type in raw_types:
            if not isinstance(raw_type, type):
                raise TypeError(f"插件事件类型必须是类，实际为: {raw_type!r}")
            event_types.append(cast(type[AllEvent], raw_type))
        return tuple(event_types)

    def _load_plugins(self) -> None:
        """载入插件并建立事件类型到插件队列的映射。"""
        for plugin in self.plugin_objects:
            param_name, annotation = self._get_event_parameter(plugin.run)
            for event_type in self._resolve_event_types(annotation):
                self.handlers_map[event_type].append((plugin.add_to_queue, param_name))
