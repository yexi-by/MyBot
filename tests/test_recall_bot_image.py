"""机器人自发图片撤回插件测试。"""

import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, cast

from app.config import EmptyPluginConfig
from app.database import GroupDataScope, StoredGroupMessage
from app.models import (
    GroupMessage,
    Image,
    MessageSegment,
    Reply,
    Response,
    Sender,
    Text,
)
from app.plugins.base import Context
from app.plugins.recall_bot_image.recall_bot_image import (
    RECALL_COMMAND,
    RecallBotImagePlugin,
)
from tests.config_helpers import (
    FakeConfigManager,
    build_plugin_snapshot,
    plugin_config_view,
)

BOT_ID = "10000"
USER_ID = "20000"
GROUP_ID = "40000"
COMMAND_MESSAGE_ID = "50000"
TARGET_MESSAGE_ID = "90000"


def build_group_message(
    *,
    message_id: str,
    user_id: str,
    message: list[MessageSegment],
    post_type: Literal["message", "message_sent"] = "message",
) -> GroupMessage:
    """构造插件测试所需的群消息。"""
    return GroupMessage(
        time=1_777_132_900,
        self_id=BOT_ID,
        post_type=post_type,
        message_type="group",
        sub_type="normal",
        user_id=user_id,
        message_id=message_id,
        group_id=GROUP_ID,
        group_name="测试群",
        message=message,
        raw_message="",
        sender=Sender(user_id=user_id, nickname=f"用户{user_id}", role="member"),
    )


def build_command_message(
    *,
    text: str = RECALL_COMMAND,
    reply_id: str | None = TARGET_MESSAGE_ID,
    post_type: Literal["message", "message_sent"] = "message",
) -> GroupMessage:
    """构造用户发送的引用撤回指令。"""
    segments: list[MessageSegment] = []
    if reply_id is not None:
        segments.append(cast(MessageSegment, Reply.new(reply_id)))
    segments.append(cast(MessageSegment, Text.new(text)))
    return build_group_message(
        message_id=COMMAND_MESSAGE_ID,
        user_id=USER_ID,
        message=segments,
        post_type=post_type,
    )


def build_target_message(
    *,
    user_id: str = BOT_ID,
    post_type: Literal["message", "message_sent"] = "message_sent",
    include_image: bool = True,
) -> GroupMessage:
    """构造被引用的机器人出站消息或不合法对照消息。"""
    segments: list[MessageSegment]
    if include_image:
        segments = [cast(MessageSegment, Image.new("cached-image.jpg"))]
    else:
        segments = [cast(MessageSegment, Text.new("普通文本"))]
    return build_group_message(
        message_id=TARGET_MESSAGE_ID,
        user_id=user_id,
        message=segments,
        post_type=post_type,
    )


def to_stored_message(message: GroupMessage) -> StoredGroupMessage:
    """把群事件转成撤回插件查询使用的 DTO。"""
    return StoredGroupMessage(
        row_id=1,
        scope=GroupDataScope(
            bot_id=message.self_id,
            group_id=message.group_id,
        ),
        message_id=message.message_id,
        group_name=message.group_name,
        sender_id=message.user_id,
        sender_name=message.sender.card or message.sender.nickname,
        sender_role=message.sender.role,
        occurred_at=datetime.fromtimestamp(message.time, tz=timezone.utc),
        direction=(
            "outgoing" if message.post_type == "message_sent" else "incoming"
        ),
        segments=tuple(message.message),
        images=(),
    )


@dataclass(slots=True)
class Feedback:
    """记录插件发送的一次群内反馈。"""

    group_id: str
    user_id: str
    text: str


class FakeBot:
    """模拟带回包撤回接口和群消息发送。"""

    def __init__(
        self,
        *,
        recall_response: Response | None = None,
        recall_error: Exception | None = None,
    ) -> None:
        """保存预设撤回结果与调用记录。"""
        self.boot_id = BOT_ID
        self.recall_response = recall_response or Response(status="ok", retcode=0)
        self.recall_error = recall_error
        self.recalled_message_ids: list[str] = []
        self.feedback: list[Feedback] = []

    async def delete_msg_with_response(self, message_id: str) -> Response:
        """记录撤回目标并返回预设 NapCat 回包。"""
        self.recalled_message_ids.append(message_id)
        if self.recall_error is not None:
            raise self.recall_error
        return self.recall_response

    async def send_msg(
        self,
        *,
        group_id: str,
        at: str,
        text: str,
    ) -> Response:
        """记录群内反馈。"""
        self.feedback.append(Feedback(group_id=group_id, user_id=at, text=text))
        return Response(status="ok", retcode=0, data={"message_id": "feedback"})


class FakeDatabase:
    """按消息 ID 返回预设的群消息。"""

    def __init__(
        self,
        *,
        stored_message: GroupMessage | None,
        search_error: Exception | None = None,
    ) -> None:
        """保存查询结果与调用记录。"""
        self.stored_message = (
            to_stored_message(stored_message) if stored_message is not None else None
        )
        self.search_error = search_error
        self.searches: list[tuple[str, str, str]] = []

    async def get_active(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
    ) -> StoredGroupMessage | None:
        """记录查询范围并返回预设结果。"""
        self.searches.append((scope.bot_id, scope.group_id, message_id))
        if self.search_error is not None:
            raise self.search_error
        return self.stored_message


class FakeContext:
    """提供图片撤回插件需要的最小运行期依赖。"""

    def __init__(self, *, bot: FakeBot, database: FakeDatabase) -> None:
        """绑定测试 Bot 和消息数据库。"""
        self.bot = bot
        self.group_messages = database


class RecallBotImagePluginTest(unittest.IsolatedAsyncioTestCase):
    """验证撤回触发、安全边界和腾讯失败回填。"""

    plugin: RecallBotImagePlugin
    bot: FakeBot
    database: FakeDatabase

    async def asyncTearDown(self) -> None:
        """停止测试实例创建的插件消费者。"""
        plugin = getattr(self, "plugin", None)
        if plugin is not None:
            await plugin.stop_consumers()

    def create_plugin(
        self,
        *,
        stored_message: GroupMessage | None,
        recall_response: Response | None = None,
        recall_error: Exception | None = None,
        search_error: Exception | None = None,
    ) -> RecallBotImagePlugin:
        """创建带预设依赖的插件实例。"""
        self.bot = FakeBot(
            recall_response=recall_response,
            recall_error=recall_error,
        )
        self.database = FakeDatabase(
            stored_message=stored_message,
            search_error=search_error,
        )
        context = FakeContext(bot=self.bot, database=self.database)
        self.plugin = RecallBotImagePlugin(
            context=cast(Context, context),
            plugin_config=plugin_config_view(
                FakeConfigManager(
                    build_plugin_snapshot(
                        recall_bot_image=EmptyPluginConfig()
                    )
                ),
                plugin_id="recall_bot_image",
            ),
        )
        return self.plugin

    async def test_exact_reply_command_recalls_bot_image(self) -> None:
        """引用机器人图片并发送精确指令时执行撤回。"""
        plugin = self.create_plugin(stored_message=build_target_message())

        handled = await plugin.run(build_command_message(text="  #撤回 \n"))

        self.assertTrue(handled)
        self.assertEqual(
            self.database.searches,
            [(BOT_ID, GROUP_ID, TARGET_MESSAGE_ID)],
        )
        self.assertEqual(self.bot.recalled_message_ids, [TARGET_MESSAGE_ID])
        self.assertEqual(self.bot.feedback[-1].text, "图片已撤回。")

    async def test_unrelated_or_extended_text_does_not_trigger(self) -> None:
        """普通消息和带额外文字的近似指令不得误触发。"""
        plugin = self.create_plugin(stored_message=build_target_message())

        unrelated_handled = await plugin.run(build_command_message(text="聊天消息"))
        extended_handled = await plugin.run(build_command_message(text="#撤回一下"))

        self.assertFalse(unrelated_handled)
        self.assertFalse(extended_handled)
        self.assertEqual(self.database.searches, [])
        self.assertEqual(self.bot.recalled_message_ids, [])
        self.assertEqual(self.bot.feedback, [])

    async def test_command_without_reply_returns_usage_feedback(self) -> None:
        """没有引用目标时只返回明确用法，不调用撤回接口。"""
        plugin = self.create_plugin(stored_message=build_target_message())

        handled = await plugin.run(build_command_message(reply_id=None))

        self.assertTrue(handled)
        self.assertEqual(self.database.searches, [])
        self.assertEqual(self.bot.recalled_message_ids, [])
        self.assertIn("请回复机器人发送的图片", self.bot.feedback[-1].text)

    async def test_user_image_cannot_be_recalled(self) -> None:
        """被引用图片不是机器人出站消息时拒绝撤回。"""
        target = build_target_message(user_id=USER_ID, post_type="message")
        plugin = self.create_plugin(stored_message=target)

        handled = await plugin.run(build_command_message())

        self.assertTrue(handled)
        self.assertEqual(self.bot.recalled_message_ids, [])
        self.assertEqual(
            self.bot.feedback[-1].text,
            "只能撤回当前机器人自己发送的图片。",
        )

    async def test_bot_text_message_cannot_be_recalled(self) -> None:
        """机器人自发纯文本消息不属于图片撤回范围。"""
        plugin = self.create_plugin(
            stored_message=build_target_message(include_image=False)
        )

        handled = await plugin.run(build_command_message())

        self.assertTrue(handled)
        self.assertEqual(self.bot.recalled_message_ids, [])
        self.assertIn("不包含图片", self.bot.feedback[-1].text)

    async def test_missing_active_message_fails_safely(self) -> None:
        """消息不存在或已撤回时不得直接撤回引用 ID。"""
        plugin = self.create_plugin(stored_message=None)

        handled = await plugin.run(build_command_message())

        self.assertTrue(handled)
        self.assertEqual(self.bot.recalled_message_ids, [])
        self.assertIn("或该消息已撤回", self.bot.feedback[-1].text)

    async def test_napcat_rejection_is_reported_as_failure(self) -> None:
        """腾讯或 NapCat 拒绝撤回时展示回包原因，不伪装成功。"""
        plugin = self.create_plugin(
            stored_message=build_target_message(),
            recall_response=Response(
                status="failed",
                retcode=100,
                message="message recall failed",
                wording="消息已超过撤回时限",
            ),
        )

        handled = await plugin.run(build_command_message())

        self.assertTrue(handled)
        self.assertEqual(self.bot.recalled_message_ids, [TARGET_MESSAGE_ID])
        self.assertIn("消息已超过撤回时限", self.bot.feedback[-1].text)
        self.assertNotIn("图片已撤回", self.bot.feedback[-1].text)

    async def test_recall_timeout_is_reported_as_failure(self) -> None:
        """等待撤回回包超时时返回可理解的失败提示。"""
        plugin = self.create_plugin(
            stored_message=build_target_message(),
            recall_error=TimeoutError("等待 NapCat 响应超时"),
        )

        handled = await plugin.run(build_command_message())

        self.assertTrue(handled)
        self.assertEqual(self.bot.recalled_message_ids, [TARGET_MESSAGE_ID])
        self.assertIn("等待 NapCat 响应超时", self.bot.feedback[-1].text)
        self.assertIn("QQ 撤回时限", self.bot.feedback[-1].text)

    async def test_outgoing_command_event_does_not_trigger(self) -> None:
        """机器人自身的 message_sent 事件不得触发指令。"""
        plugin = self.create_plugin(stored_message=build_target_message())

        handled = await plugin.run(build_command_message(post_type="message_sent"))

        self.assertFalse(handled)
        self.assertEqual(self.database.searches, [])
        self.assertEqual(self.bot.recalled_message_ids, [])


if __name__ == "__main__":
    unittest.main()
