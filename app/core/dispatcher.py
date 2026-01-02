from app.api import BOTClient
from app.models import AllEvent

from .plugin_manager import PluginController


class EventDispatcher:
    """中央逻辑处理器"""

    def __init__(self, plugincontroller: PluginController, bot: BOTClient) -> None:
        self.plugincontroller = plugincontroller
        self.bot = bot

    async def dispatch_event(self, event: AllEvent) -> None:
        event_type = type(event)
        handlers = self.plugincontroller.handlers_map.get(event_type, [])
        for handler, param_name in handlers:
            if await handler(**{param_name: event}):
                return
