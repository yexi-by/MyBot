"""事件分发器。"""

from app.api import BOTClient
from app.models import AllEvent

from .plugin_manager import PluginController


class EventDispatcher:
    """按事件类型把 NapCat 事件交给插件链处理。"""

    def __init__(self, plugincontroller: PluginController, bot: BOTClient) -> None:
        """保存插件控制器和机器人客户端。"""
        self.plugincontroller: PluginController = plugincontroller
        self.bot: BOTClient = bot

    async def dispatch_event(self, event: AllEvent) -> None:
        """按插件优先级依次分发事件，插件返回 True 时终止链路。"""
        event_type = type(event)
        handlers = self.plugincontroller.handlers_map.get(event_type, [])
        for handler, param_name in handlers:
            if await handler(**{param_name: event}):
                return
