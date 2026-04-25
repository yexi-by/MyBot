"""NapCat 群聊本地工具测试。"""

import unittest
from typing import cast

from app.models import GroupMessage, JsonObject, MessageSegment, NapCatId, Response, Sender, Text
from app.services.napcat.group_tools import (
    GetGroupHistoryMessagesArgs,
    NapCatGroupToolExecutor,
    NapCatGroupToolBot,
)
from app.services.napcat.group_tools.protocols import CachedNapCatMessage


def build_group_message(*, text: str = "hello") -> GroupMessage:
    """构造测试用群消息。"""
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
        message=[Text.new(text)],
        raw_message=text,
        sender=Sender(user_id="20000", nickname="夜袭", role="member"),
    )


class FakeBot:
    """测试用 NapCat Bot。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.sent_count = 0

    async def send_msg(
        self,
        *,
        group_id: NapCatId,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """记录发送动作并返回成功响应。"""
        _ = (group_id, message_segment)
        self.sent_count += 1
        return Response(status="ok", retcode=0)

    async def get_group_root_files(
        self, group_id: NapCatId, file_count: int = 50
    ) -> Response:
        """返回空群文件列表。"""
        _ = (group_id, file_count)
        return Response(status="ok", retcode=0, data=[])

    async def get_group_files_by_folder(
        self,
        group_id: NapCatId,
        folder_id: str | None = None,
        folder: str | None = None,
        file_count: int = 50,
    ) -> Response:
        """返回空文件夹列表。"""
        _ = (group_id, folder_id, folder, file_count)
        return Response(status="ok", retcode=0, data=[])

    async def get_group_file_url(self, group_id: NapCatId, file_id: str) -> Response:
        """返回测试下载链接。"""
        _ = (group_id, file_id)
        return Response(status="ok", retcode=0, data={"url": "https://example.com/a"})


class FakeDatabase:
    """测试用 Redis 历史消息数据库。"""

    async def search_messages(
        self,
        *,
        self_id: str,
        message_id: str | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: str | None = None,
        user_id: str | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> CachedNapCatMessage | list[CachedNapCatMessage] | None:
        """返回空历史。"""
        _ = (
            self_id,
            message_id,
            root,
            limit_tuple,
            group_id,
            user_id,
            max_time,
            min_time,
        )
        return None


class NapCatGroupToolExecutorTest(unittest.IsolatedAsyncioTestCase):
    """验证 NapCat 本地工具行为。"""

    async def test_modifier_tools_build_reply_at_and_spaced_text(self) -> None:
        """回复和艾特工具会修饰最终消息，并在正文前补空格。"""
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        _ = await executor.call_tool("qq__reply_current_message", {})
        _ = await executor.call_tool("qq__mention_user", {"user_id": "20000"})
        segments = executor.build_final_message_segments("测试通过")

        self.assertEqual([segment.type for segment in segments], ["reply", "at", "text"])
        text_segment = segments[-1]
        self.assertIsInstance(text_segment, Text)
        text_segment = cast(Text, text_segment)
        self.assertEqual(text_segment.data.text, " 测试通过")

    async def test_mention_all_disabled_returns_model_visible_error(self) -> None:
        """关闭 @全体 时工具返回模型可读错误。"""
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=FakeDatabase(),
            event=build_group_message(),
            allow_mention_all=False,
        )

        result = await executor.call_tool("qq__mention_user", {"user_id": "all"})

        self.assertIsInstance(result, dict)
        result_dict = cast(JsonObject, result)
        self.assertIs(result_dict["ok"], False)
        error = result_dict["error"]
        if not isinstance(error, str):
            raise AssertionError("工具错误信息必须是字符串")
        self.assertIn("@全体", error)

    def test_history_duration_requires_minutes(self) -> None:
        """按分钟查询历史时必须明确分钟数。"""
        with self.assertRaises(ValueError):
            GetGroupHistoryMessagesArgs.model_validate(
                {"query_mode": "recent_duration"}
            )
