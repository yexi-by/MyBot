"""AI 群聊输入构造测试。"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

import httpx

from app.config import AIGroupChatConfig, AIGroupConfig
from app.database import GroupDataScope, GroupMessageReader, StoredGroupMessage
from app.models import (
    At,
    File,
    Forward,
    GroupMessage,
    Image,
    Json,
    LightApp,
    MFace,
    Markdown,
    MessageSegment,
    Node,
    Reply,
    Response,
    Sender,
    Share,
    Text,
    UnknownSegment,
    to_json_value,
)
from app.plugins.ai_group_chat.message_builder import GroupChatMessageBuilder
from tests.config_helpers import build_ai_group_chat_config

VISION_SYSTEM_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/system.md"
VISION_USER_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/user.md"
# 测试消息时间为 1_777_132_900，进程上下文起点设在其前 10 分钟。
CONTEXT_STARTED_AT = datetime.fromtimestamp(1_777_132_300, tz=timezone.utc)


class EmptyDatabase:
    """测试用空消息数据库；未实现 list_around，未开启最近群聊时不得读取。"""

    async def get_active(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
    ) -> StoredGroupMessage | None:
        """不返回引用消息。"""
        _ = (scope, message_id)
        return None


class ReplyDatabase(EmptyDatabase):
    """测试用引用消息数据库。"""

    def __init__(self, reply_message: GroupMessage) -> None:
        """保存固定引用消息。"""
        self.reply_message: StoredGroupMessage = to_stored_message(reply_message)

    async def get_active(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
    ) -> StoredGroupMessage:
        """返回固定引用消息。"""
        _ = (scope, message_id)
        return self.reply_message


class RecentChatDatabase(EmptyDatabase):
    """按锚点返回固定群聊记录，并记录读取范围。"""

    def __init__(self, messages: list[StoredGroupMessage]) -> None:
        """保存按时间正序排列、包含锚点的群聊记录。"""
        self.messages: list[StoredGroupMessage] = messages
        self.around_calls: list[tuple[GroupDataScope, str, int, int]] = []

    async def list_around(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        before_count: int,
        after_count: int,
        sender_id: str | None = None,
    ) -> list[StoredGroupMessage]:
        """模拟仓库：返回锚点前 before_count 条与锚点本身。"""
        _ = sender_id
        self.around_calls.append((scope, message_id, before_count, after_count))
        anchor_index = next(
            index
            for index, message in enumerate(self.messages)
            if message.message_id == message_id
        )
        start = max(0, anchor_index - before_count)
        return self.messages[start : anchor_index + 1]


class MissingImageBot:
    """测试用媒体接口，所有刷新请求都返回资源不存在。"""

    async def get_file(self, file_id: str | None = None, file: str | None = None) -> Response:
        return Response(status="failed", retcode=404)

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """返回可恢复的图片读取失败。"""
        _ = (file_id, file)
        return Response(status="failed", retcode=404, message="图片不存在")


def build_config(**overrides: object) -> AIGroupChatConfig:
    """构造使用独立视觉工具的测试配置。"""
    return build_ai_group_chat_config(
        provider="main-vendor",
        model_name="text-model",
        overrides={
            "vision": {
                "model": {"provider": "vision-vendor", "name": "vision-model"},
                "system_prompt_file": VISION_SYSTEM_PROMPT_PATH,
                "user_prompt_file": VISION_USER_PROMPT_PATH,
            },
            "groups": [
            AIGroupConfig(
                id="40000",
                system_prompt_file="unused",
                max_context_tokens=1000000,
            )
            ],
            **overrides,
        },
    )


def build_message(
    *,
    message: list[MessageSegment] | None = None,
    raw_message: str = "你好呀",
    message_id: str = "30000",
    user_id: str = "20000",
    nickname: str = "夜袭",
    time: int = 1_777_132_900,
    post_type: Literal["message", "message_sent"] = "message",
) -> GroupMessage:
    """构造测试用群消息。"""
    return GroupMessage(
        time=time,
        self_id="10000",
        post_type=post_type,
        message_type="group",
        sub_type="normal",
        user_id=user_id,
        message_id=message_id,
        group_id="40000",
        group_name="测试群",
        message=message if message is not None else [Text.new("你好呀")],
        raw_message=raw_message,
        sender=Sender(user_id=user_id, nickname=nickname, role="member"),
    )


def to_stored_message(message: GroupMessage) -> StoredGroupMessage:
    """把测试群事件转成存储层 DTO。"""
    sender_name = message.sender.card or message.sender.nickname
    return StoredGroupMessage(
        row_id=1,
        scope=GroupDataScope(
            bot_id=message.self_id,
            group_id=message.group_id,
        ),
        message_id=message.message_id,
        group_name=message.group_name,
        sender_id=message.user_id,
        sender_name=sender_name,
        sender_role=message.sender.role,
        occurred_at=datetime.fromtimestamp(message.time, tz=timezone.utc),
        direction=(
            "outgoing" if message.post_type == "message_sent" else "incoming"
        ),
        segments=tuple(message.message),
        media=(),
    )


def build_builder(
    *,
    database: EmptyDatabase,
    config: AIGroupChatConfig | None = None,
) -> GroupChatMessageBuilder:
    """构造使用固定图片 Bot 的消息输入构造器。"""
    return GroupChatMessageBuilder(
        config=config if config is not None else build_config(),
        group_messages=cast(GroupMessageReader, database),
        bot=MissingImageBot(),
        http_client=cast(httpx.AsyncClient, object()),
        context_started_at=CONTEXT_STARTED_AT,
    )


class GroupChatMessageBuilderTest(unittest.TestCase):
    """验证群消息文本、图片来源顺序和截断规则。"""

    def test_current_message_is_a_chat_line_with_outer_message_id(self) -> None:
        """当前消息写成聊天记录：标题行带工具需要的消息 ID 和 `<At>` 需要的 QQ 号。"""
        result = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(
                msg=build_message()
            )
        )

        header, _, body = (result.turn_messages[0].text or "").partition("\n")
        self.assertIn("夜袭(20000)", header)
        self.assertIn("消息 ID 30000", header)
        self.assertEqual(body, "你好呀")
        self.assertEqual(result.recent_chat_messages, [])

    def test_mentions_keep_other_members_but_drop_the_bot(self) -> None:
        """艾特机器人已由标题表达；艾特其他群员保留 QQ 号，模型才知道“他”指谁。"""
        msg = build_message(
            message=[
                At.new("10000"),
                At.new("20001", name="小明"),
                Text.new(" 你看他"),
            ]
        )

        text = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(msg=msg)
        ).turn_messages[0].text or ""

        body = text.partition("\n")[2]
        self.assertIn("小明", body)
        self.assertIn("20001", body)
        self.assertIn("你看他", body)
        self.assertNotIn("10000", body)

    def test_recent_chat_only_covers_this_run_before_current_message(self) -> None:
        """最近群聊绑定当前机器人和群，只取本进程启动后、当前消息之前的消息。"""
        stored = [
            to_stored_message(message)
            for message in (
                build_message(
                    message_id="29990",
                    message=[Text.new("重启前的消息")],
                    time=1_777_132_200,
                ),
                build_message(
                    message_id="29991",
                    user_id="20001",
                    nickname="小明",
                    message=[Text.new("今天食堂\n又涨价了")],
                    time=1_777_132_800,
                ),
                build_message(
                    message_id="29992",
                    user_id="10000",
                    nickname="机器人",
                    post_type="message_sent",
                    message=[Reply.new("29991"), Text.new("涨了多少")],
                    time=1_777_132_850,
                ),
                build_message(
                    message_id="29993",
                    user_id="20001",
                    nickname="小明",
                    message=[
                        At.new("10000"),
                        Image.new("sticker.gif", summary="[动画表情]"),
                    ],
                    time=1_777_132_860,
                ),
                build_message(),
            )
        ]
        database = RecentChatDatabase(stored)

        result = asyncio.run(
            build_builder(
                database=database,
                config=build_config(
                    recent_chat={"max_messages": 10, "max_chars_per_message": 0}
                ),
            ).build_turn_messages(msg=build_message())
        )

        self.assertEqual(
            database.around_calls,
            [(GroupDataScope(bot_id="10000", group_id="40000"), "30000", 10, 0)],
        )
        self.assertEqual(len(result.recent_chat_messages), 1)
        recent_text = result.recent_chat_messages[0].text or ""
        self.assertEqual(
            [
                line.split("] ", 1)[1]
                for line in recent_text.splitlines()
                if line.startswith("[")
            ],
            [
                "小明(20001)：今天食堂 又涨价了",
                "你：[回复 小明] 涨了多少",
                "小明(20001)：@你 [动画表情]",
            ],
        )
        self.assertNotIn("重启前的消息", recent_text)
        self.assertNotIn("今天食堂", result.turn_messages[0].text or "")

    def test_recent_chat_and_quotes_isolate_only_abnormal_bot_text(self) -> None:
        """自动最近群聊与引用都隔离旧泄漏，成员讨论和正常机器人发言仍保留。"""
        leaked = build_message(
            message_id="29991", user_id="10000", post_type="message_sent",
            message=[Text.new("🫧"), Text.new("thought\nprivate-leaked-history")],
            time=1_777_132_800,
        )
        original = leaked.model_copy(deep=True)
        current = build_message(message=[
            Reply.new(leaked.message_id), At.new("10000"), Text.new("请继续正常聊天"),
        ])
        records = [to_stored_message(message) for message in (
            leaked,
            build_message(
                message_id="29992", user_id="10000", post_type="message_sent",
                message=[Text.new("正常约定周六出发")], time=1_777_132_820,
            ),
            build_message(
                message_id="29993", message=[Text.new("🫧thought\n成员讨论内容")],
                time=1_777_132_840,
            ),
            current,
        )]
        database = RecentChatDatabase(records)
        recent = asyncio.run(build_builder(
            database=database,
            config=build_config(recent_chat={"max_messages": 10, "max_chars_per_message": 0}),
        ).build_turn_messages(msg=current))
        recent_text = recent.recent_chat_messages[0].text or ""
        self.assertNotIn("private-leaked-history", recent_text)
        self.assertIn("正常约定周六出发", recent_text)
        self.assertIn("成员讨论内容", recent_text)
        self.assertEqual(database.messages, records)

        for quoted_segments in (
            leaked.message,
            [Forward.new("cached-reply", content=to_json_value([
                Node.new(user_id="10000", nickname="机器人", content=leaked.message),
            ]))],
        ):
            with self.subTest(segments=quoted_segments):
                quoted = leaked.model_copy(update={"message": quoted_segments})
                quote_database = ReplyDatabase(quoted)
                reply = asyncio.run(build_builder(
                    database=quote_database,
                ).build_turn_messages(msg=current))
                reply_text = reply.turn_messages[0].text or ""
                self.assertNotIn("private-leaked-history", reply_text)
                self.assertIn(leaked.message_id, reply_text)
                self.assertIn("请继续正常聊天", reply_text)
                self.assertEqual(quote_database.reply_message.segments, tuple(quoted_segments))
        self.assertEqual(leaked, original)

    def test_recent_chat_is_empty_right_after_restart(self) -> None:
        """启动后还没有新消息时不附带群聊记录，重启后的上下文保持干净。"""
        database = RecentChatDatabase(
            [
                to_stored_message(
                    build_message(
                        message_id="29990",
                        message=[Text.new("重启前的消息")],
                        time=1_777_132_200,
                    )
                ),
                to_stored_message(build_message()),
            ]
        )

        result = asyncio.run(
            build_builder(
                database=database,
                config=build_config(
                    recent_chat={"max_messages": 10, "max_chars_per_message": 0}
                ),
            ).build_turn_messages(msg=build_message())
        )

        self.assertEqual(result.recent_chat_messages, [])

    def test_unreadable_image_returns_structured_error(self) -> None:
        """图片读取失败时保留来源标签和可恢复错误。"""
        msg = build_message(
            message=[Text.new("看看图"), Image.new("image.png")],
            raw_message="看看图[图片]",
        )

        result = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(msg=msg)
        )

        self.assertEqual(result.detected_image_count, 1)
        self.assertEqual(result.loaded_image_count, 0)
        self.assertEqual(len(result.image_errors), 1)
        self.assertEqual(result.image_errors[0].label, "当前消息第 1 张图片")
        self.assertIn("当前请求未附带图片内容", result.turn_messages[0].text or "")

    def test_current_and_reply_images_keep_source_order(self) -> None:
        """当前消息图片排在引用消息图片之前，并保留来源标签。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_path = root / "current.bin"
            reply_path = root / "reply.bin"
            current_path.write_bytes(b"current-image")
            reply_path.write_bytes(b"reply-image")
            quoted = build_message(
                message=[Image.new("reply.png", path=str(reply_path))],
                raw_message="[图片]",
            )
            msg = build_message(
                message=[
                    Text.new("看看两张图"),
                    Image.new("current.png", path=str(current_path)),
                    Reply.new("reply-message-id"),
                ],
                raw_message="看看两张图[图片]",
            )

            result = asyncio.run(
                build_builder(database=ReplyDatabase(quoted)).build_turn_messages(
                    msg=msg
                )
            )

        self.assertEqual(result.detected_image_count, 2)
        self.assertEqual(result.loaded_image_count, 2)
        self.assertEqual(
            [artifact.image_bytes for artifact in result.image_artifacts],
            [b"current-image", b"reply-image"],
        )
        self.assertEqual(
            [artifact.label for artifact in result.image_artifacts],
            ["当前消息第 1 张图片", "引用消息第 1 张图片"],
        )
        self.assertIsNone(result.turn_messages[0].image)

    def test_builder_reads_all_images_before_turn_delivery_policy(self) -> None:
        """消息构造器不提前截断，统一由视觉交付层执行单轮策略。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = [root / f"image-{index}.bin" for index in range(3)]
            for index, path in enumerate(paths):
                path.write_bytes(f"image-{index}".encode())
            quoted = build_message(
                message=[Image.new("reply.png", path=str(paths[2]))],
                raw_message="[图片]",
            )
            msg = build_message(
                message=[
                    Image.new("current-1.png", path=str(paths[0])),
                    Image.new("current-2.png", path=str(paths[1])),
                    Reply.new("reply-message-id"),
                ],
                raw_message="[图片][图片]",
            )

            result = asyncio.run(
                build_builder(
                    database=ReplyDatabase(quoted),
                    config=build_config(images={"max_per_turn": 2}),
                ).build_turn_messages(msg=msg)
            )

        self.assertEqual(result.detected_image_count, 3)
        self.assertEqual(result.truncated_image_count, 0)
        self.assertEqual(
            [artifact.label for artifact in result.image_artifacts],
            [
                "当前消息第 1 张图片",
                "当前消息第 2 张图片",
                "引用消息第 1 张图片",
            ],
        )

    def test_supported_non_text_segments_are_readable(self) -> None:
        """卡片、文件、商城表情和 Markdown 会进入 LLM 可读文本。"""
        msg = build_message(
            message=[
                Share.new(
                    "https://example.com/share",
                    title="链接标题",
                    content="链接描述",
                ),
                Forward.new(
                    "forward-1",
                    content=[{"type": "text", "data": {"text": "转发正文"}}],
                ),
                Json.new({"title": "JSON 标题", "desc": "JSON 描述"}),
                MFace.new("emoji-1", summary="表情摘要"),
                File.new("report.pdf", name="报告.pdf", file_size=2048),
                Markdown.new("# 标题\n正文"),
                LightApp.new({"title": "小程序标题", "desc": "小程序描述"}),
            ],
            raw_message="[复杂消息]",
        )

        chat_message = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(msg=msg)
        ).turn_messages[0]
        text = chat_message.text or ""

        for expected in (
            "链接标题",
            "转发正文",
            "JSON 标题",
            "表情摘要",
            "报告.pdf",
            "2.0 KB",
            "# 标题",
            "小程序标题",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_forward_without_content_uses_outer_message_id(self) -> None:
        """没有内嵌内容的合并转发提示使用外层群消息 ID。"""
        msg = build_message(
            message=[Forward.new("forward-empty")],
            raw_message="[合并转发]",
        )

        chat_message = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(msg=msg)
        ).turn_messages[0]
        text = chat_message.text or ""

        self.assertIn("消息 ID 30000", text)
        self.assertIn("合并转发消息", text)
        self.assertNotIn("forward-empty", text)
        self.assertIn("该 Forward 所在群消息", text)
        self.assertIn("qq__get_forward_message", text)

    def test_quoted_bot_forward_with_cached_content_is_expanded(self) -> None:
        """引用机器人的缓存合并转发时直接展示其中正文。"""
        quoted = GroupMessage(
            time=1_777_132_899,
            self_id="10000",
            post_type="message_sent",
            message_type="group",
            sub_type="normal",
            user_id="10000",
            message_id="90004",
            group_id="40000",
            group_name="测试群",
            message=[
                Forward.new(
                    "forward-90004",
                    content=to_json_value(
                        [
                            Node.new(
                                user_id="10000",
                                nickname="机器人",
                                content=[Text.new("机器人先前的长回复")],
                            )
                        ]
                    ),
                )
            ],
            raw_message="[合并转发]",
            sender=Sender(user_id="10000", nickname="机器人"),
        )
        current = build_message(
            message=[
                Reply.new("90004"),
                At.new("10000"),
                Text.new("请继续解释"),
            ]
        )

        chat_message = asyncio.run(
            build_builder(database=ReplyDatabase(quoted)).build_turn_messages(
                msg=current
            )
        ).turn_messages[0]
        text = chat_message.text or ""

        quote_lines = [line for line in text.splitlines() if line.startswith(">")]
        self.assertIn("你之前的消息", quote_lines[0])
        self.assertIn("消息 ID 90004", quote_lines[0])
        self.assertIn("机器人先前的长回复", "\n".join(quote_lines))
        self.assertNotIn("请继续解释", "\n".join(quote_lines))
        self.assertNotIn("qq__get_forward_message", text)

    def test_forward_content_limit_marks_omitted_items(self) -> None:
        """合并转发超过展开上限时会标明已省略。"""
        items = [
            {
                "sender": {"nickname": f"用户{index}"},
                "message": [{"type": "text", "data": {"text": f"第{index}条"}}],
            }
            for index in range(1, 11)
        ]
        msg = build_message(message=[Forward.new("forward-many", content=items)])

        chat_message = asyncio.run(
            build_builder(
                database=EmptyDatabase(),
                config=build_config(formatting={"forward_max_items": 8}),
            ).build_turn_messages(msg=msg)
        ).turn_messages[0]
        text = chat_message.text or ""

        self.assertIn("第8条", text)
        self.assertNotIn("第9条", text)
        self.assertIn("其余 2 条合并转发内容已省略", text)

    def test_unknown_segment_is_visible_to_model(self) -> None:
        """未支持消息段会以占位摘要进入 LLM 输入。"""
        msg = build_message(
            message=[UnknownSegment(type="custom_type", data={"value": "保留"})]
        )

        chat_message = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(msg=msg)
        ).turn_messages[0]
        text = chat_message.text or ""

        self.assertIn("暂不支持的消息段: custom_type", text)
        self.assertIn("保留", text)


if __name__ == "__main__":
    unittest.main()
