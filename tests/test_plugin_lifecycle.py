"""插件消费者生命周期测试。"""

import asyncio
import unittest
from typing import cast

from app.models import AllEvent, GroupMessage, Sender, Text
from app.plugins.base import PLUGINS, BasePlugin, Context


def build_group_message(message_id: str) -> GroupMessage:
    """构造生命周期测试所需的最小群消息。"""
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id=message_id,
        group_id="40000",
        group_name="测试群",
        message=[Text.new("测试消息")],
        raw_message="测试消息",
        sender=Sender(user_id="20000", nickname="测试用户", role="member"),
    )


class LifecyclePlugin(BasePlugin[AllEvent]):
    """通过事件控制运行时机的测试插件。"""

    name = "消费者生命周期测试插件"
    consumers_count = 1
    priority = 0

    started: asyncio.Event
    release: asyncio.Event
    received_messages: list[AllEvent]

    def setup(self) -> None:
        """初始化测试同步事件和调用记录。"""
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.received_messages = []

    async def run(self, msg: AllEvent) -> bool:
        """等待测试放行后返回成功。"""
        self.received_messages.append(msg)
        self.started.set()
        await self.release.wait()
        return True


# 测试插件不应污染应用运行期的插件自动发现列表。
PLUGINS.remove(LifecyclePlugin)


class PluginLifecycleTest(unittest.IsolatedAsyncioTestCase):
    """验证消费者正常处理和关闭时的 Future 收尾行为。"""

    async def asyncSetUp(self) -> None:
        """为每个用例创建独立插件实例。"""
        self.plugin = LifecyclePlugin(context=cast(Context, object()))

    async def asyncTearDown(self) -> None:
        """确保用例结束后没有残留消费者任务。"""
        await self.plugin.stop_consumers()

    async def test_normal_task_returns_result_and_consumer_remains_available(
        self,
    ) -> None:
        """正常任务应返回处理结果，消费者随后仍可继续工作。"""
        self.plugin.release.set()

        first_result = await self.plugin.add_to_queue(build_group_message("30001"))
        second_result = await self.plugin.add_to_queue(build_group_message("30002"))

        self.assertTrue(first_result)
        self.assertTrue(second_result)
        self.assertEqual(len(self.plugin.received_messages), 2)
        self.assertFalse(self.plugin.consumers[0].done())

    async def test_stop_cancels_active_and_queued_callers(self) -> None:
        """停止插件时应结束活动与排队调用，并清空队列。"""
        active_task = asyncio.create_task(
            self.plugin.add_to_queue(build_group_message("30001"))
        )
        await asyncio.wait_for(self.plugin.started.wait(), timeout=1)
        queued_task = asyncio.create_task(
            self.plugin.add_to_queue(build_group_message("30002"))
        )
        await asyncio.sleep(0)
        self.assertEqual(self.plugin.task_queue.qsize(), 1)

        await self.plugin.stop_consumers()

        with self.assertRaises(asyncio.CancelledError):
            _ = await active_task
        with self.assertRaises(asyncio.CancelledError):
            _ = await queued_task
        self.assertTrue(self.plugin.task_queue.empty())
        await asyncio.wait_for(self.plugin.task_queue.join(), timeout=1)
        self.assertEqual(self.plugin.consumers, [])

    async def test_stopped_plugin_rejects_new_tasks(self) -> None:
        """插件开始关闭后不得再接受事件入队。"""
        await self.plugin.stop_consumers()

        with self.assertRaisesRegex(RuntimeError, "已停止"):
            _ = await self.plugin.add_to_queue(build_group_message("30001"))

        self.assertTrue(self.plugin.task_queue.empty())

    async def test_cancelled_caller_does_not_terminate_consumer(self) -> None:
        """单个调用方取消等待时不应导致消费者退出。"""
        cancelled_task = asyncio.create_task(
            self.plugin.add_to_queue(build_group_message("30001"))
        )
        await asyncio.wait_for(self.plugin.started.wait(), timeout=1)
        cancelled_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await cancelled_task

        self.plugin.release.set()
        next_result = await asyncio.wait_for(
            self.plugin.add_to_queue(build_group_message("30002")), timeout=1
        )

        self.assertTrue(next_result)
        self.assertEqual(len(self.plugin.received_messages), 2)
        self.assertFalse(self.plugin.consumers[0].done())


if __name__ == "__main__":
    unittest.main()
