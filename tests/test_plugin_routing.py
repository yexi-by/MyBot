"""插件 NapCat 事件路由测试。"""

import unittest
from typing import cast

from app.api import BOTClient
from app.core.dispatcher import EventDispatcher
from app.core.plugin_manager import PluginController
from app.models import AllEvent, GroupBanEvent, GroupMessage, Sender, Text
from app.plugins.base import PLUGINS, BasePlugin, Context


class RoutingPlugin(BasePlugin[GroupMessage | GroupBanEvent]):
    """记录控制器按直接联合类型分发的事件。"""

    name = "事件路由测试插件"
    plugin_id = "event_routing_test"
    consumers_count = 1
    priority = 0

    received_events: list[GroupMessage | GroupBanEvent]

    def setup(self) -> None:
        """初始化事件记录。"""
        self.received_events = []

    async def run(self, msg: GroupMessage | GroupBanEvent) -> bool:
        """记录收到的事件并允许后续插件继续处理。"""
        self.received_events.append(msg)
        return False


# 测试插件不应进入应用运行期的插件自动发现列表。
PLUGINS.remove(cast(type[BasePlugin[AllEvent]], cast(object, RoutingPlugin)))


def build_group_message() -> GroupMessage:
    """构造路由测试所需的最小群消息。"""
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id="30000",
        group_id="40000",
        group_name="测试群",
        message=[Text.new("测试消息")],
        raw_message="测试消息",
        sender=Sender(user_id="20000", nickname="测试用户", role="member"),
    )


class PluginRoutingTest(unittest.IsolatedAsyncioTestCase):
    """验证删除内部事件总线后 NapCat 路由保持有效。"""

    async def asyncSetUp(self) -> None:
        """创建插件、控制器和事件分发器。"""
        self.plugin = RoutingPlugin(context=cast(Context, object()))
        plugin = cast(BasePlugin[AllEvent], cast(object, self.plugin))
        self.controller = PluginController(plugin_objects=[plugin])
        self.dispatcher = EventDispatcher(
            plugincontroller=self.controller,
            bot=cast(BOTClient, object()),
        )

    async def asyncTearDown(self) -> None:
        """停止测试插件的消费者任务。"""
        await self.plugin.stop_consumers()

    async def test_direct_union_annotation_routes_each_event_type(self) -> None:
        """直接联合注解中的每种 NapCat 事件都应建立路由。"""
        self.assertIn(GroupMessage, self.controller.handlers_map)
        self.assertIn(GroupBanEvent, self.controller.handlers_map)

        message = build_group_message()
        await self.dispatcher.dispatch_event(event=message)

        self.assertEqual(self.plugin.received_events, [message])


if __name__ == "__main__":
    unittest.main()
