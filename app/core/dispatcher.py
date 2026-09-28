"""事件分发器。"""

from app.api import BOTClient
from app.models import AllEvent, GroupMessage, NapCatId, PrivateMessage
from app.utils.log import log_event

from .plugin_manager import PluginController


class EventDispatcher:
    """按事件类型把 NapCat 事件交给插件链处理。"""

    def __init__(
        self,
        plugincontroller: PluginController,
        bot: BOTClient,
        blocked_user_ids: frozenset[NapCatId],
    ) -> None:
        """保存插件控制器和机器人客户端。"""
        self.plugincontroller: PluginController = plugincontroller
        self.bot: BOTClient = bot
        self.blocked_user_ids = blocked_user_ids

    async def dispatch_event(self, event: AllEvent) -> None:
        """按插件优先级依次分发事件，插件返回 True 时终止链路。"""
        if (
            isinstance(event, (GroupMessage, PrivateMessage))
            and event.post_type == "message"
            and event.user_id in self.blocked_user_ids
        ):
            log_event(
                level="DEBUG",
                event="plugin.message.blocked",
                category="plugin",
                message="发送者位于屏蔽列表，本条消息不触发插件",
                bot_id=event.self_id,
                user_id=event.user_id,
                group_id=event.group_id,
                message_id=event.message_id,
            )
            return
        event_type = type(event)
        handlers = self.plugincontroller.handlers_map.get(event_type, [])
        for handler, param_name in handlers:
            if await handler(**{param_name: event}):
                return
