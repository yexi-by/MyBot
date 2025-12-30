from typing import Any
from .event_parser import EventTypeChecker
from .plugin_manager import PluginController


class EventDispatcher:
    """中央逻辑处理器"""

    def __init__(
        self,
        checker: EventTypeChecker,
        plugincontroller: PluginController,
    ) -> None:
        self.checker = checker
        self.plugincontroller = plugincontroller

    async def dispatch_event(self, data: dict[str, Any]) -> None:
        event = self.checker.get_event(data)
        if event is None:
            return
        event_type = type(event)
        handlers = self.plugincontroller.handlers_map.get(event_type, [])
        for handler, param_name in handlers:
            if await handler(**{param_name: event}):
                return
