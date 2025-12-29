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
        for handler, dependencies in self.plugincontroller.dependencies_list:
            for name, annotation in dependencies.items():
                if not isinstance(event, annotation):
                    continue
                kwargs = {name: event}
                return_value = await handler(**kwargs)
                if return_value:
                    return
                break
