"""插件 NapCat 事件路由测试。"""

import unittest
from typing import cast

from app.api import BOTClient
from app.core.dispatcher import EventDispatcher
from app.core.plugin_manager import PluginController
from app.models import AllEvent, GroupBanEvent, GroupMessage, PrivateMessage, Sender, Text
from app.plugins.base import PLUGINS, BasePlugin, Context
from tests.config_helpers import (
    FakeConfigManager,
    build_plugin_snapshot,
    plugin_config_view,
)


class RoutingPlugin(BasePlugin[GroupMessage | PrivateMessage | GroupBanEvent]):
    """记录控制器按直接联合类型分发的事件。"""

    name = "事件路由测试插件"
    plugin_id = "event_routing_test"
    consumers_count = 1
    priority = 0

    received_events: list[GroupMessage | PrivateMessage | GroupBanEvent]

    def setup(self) -> None:
        """初始化事件记录。"""
        self.received_events = []

    async def run(self, msg: GroupMessage | PrivateMessage | GroupBanEvent) -> bool:
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
        self.plugin = RoutingPlugin(
            context=cast(Context, object()),
            plugin_config=plugin_config_view(
                FakeConfigManager(build_plugin_snapshot()),
                plugin_id="event_routing_test",
            ),
            consumers_count=1,
            stop_timeout_seconds=1,
        )
        plugin = cast(BasePlugin[AllEvent], cast(object, self.plugin))
        self.controller = PluginController(plugin_objects=[plugin])
        self.dispatcher = EventDispatcher(
            plugincontroller=self.controller,
            bot=cast(BOTClient, object()),
            blocked_user_ids=frozenset(),
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

    async def test_blocked_sender_never_reaches_group_or_private_plugins(self) -> None:
        """被屏蔽用户的群聊和私聊消息均在插件调用之前被忽略。"""
        dispatcher = EventDispatcher(
            plugincontroller=self.controller,
            bot=cast(BOTClient, object()),
            blocked_user_ids=frozenset({"20000"}),
        )
        group = build_group_message()
        private = PrivateMessage(
            time=group.time,
            self_id=group.self_id,
            post_type="message",
            message_type="private",
            user_id=group.user_id,
            message_id=group.message_id,
            message=group.message,
            sender=group.sender,
        )
        for event in (group, private):
            with self.subTest(message_type=event.message_type):
                await dispatcher.dispatch_event(event)
                self.assertEqual(self.plugin.received_events, [])

    async def test_blocking_preserves_other_users_and_notice_events(self) -> None:
        """其他用户的消息以及被屏蔽成员的通知事件继续进入插件链。"""
        dispatcher = EventDispatcher(
            plugincontroller=self.controller,
            bot=cast(BOTClient, object()),
            blocked_user_ids=frozenset({"20000"}),
        )
        other = build_group_message().model_copy(update={"user_id": "20001"})
        notice = GroupBanEvent(
            time=other.time,
            self_id=other.self_id,
            post_type="notice",
            notice_type="group_ban",
            sub_type="ban",
            group_id=other.group_id,
            user_id="20000",
            operator_id="10000",
            duration=60,
        )
        await dispatcher.dispatch_event(other)
        await dispatcher.dispatch_event(notice)
        self.assertEqual(self.plugin.received_events, [other, notice])


if __name__ == "__main__":
    unittest.main()
