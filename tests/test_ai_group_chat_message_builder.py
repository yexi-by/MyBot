"""AI 群聊输入构造测试。"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import cast

import httpx

from app.database import RedisDatabaseManager
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
from app.plugins.ai_group_chat.config import AIGroupChatConfig, GroupChatConfig
from app.plugins.ai_group_chat.message_builder import GroupChatMessageBuilder

VISION_SYSTEM_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/system.md"
VISION_USER_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/user.md"


class EmptyDatabase:
    """测试用空消息数据库。"""

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
    ) -> GroupMessage | None:
        """不返回引用消息。"""
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


class ReplyDatabase(EmptyDatabase):
    """测试用引用消息数据库。"""

    def __init__(self, reply_message: GroupMessage) -> None:
        """保存固定引用消息。"""
        self.reply_message: GroupMessage = reply_message

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
    ) -> GroupMessage:
        """返回固定引用消息。"""
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
        return self.reply_message


class MissingImageBot:
    """测试用图片接口，所有刷新请求都返回资源不存在。"""

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """返回可恢复的图片读取失败。"""
        _ = (file_id, file)
        return Response(status="failed", retcode=404, message="图片不存在")


def build_config(**overrides: object) -> AIGroupChatConfig:
    """构造使用独立视觉工具的测试配置。"""
    values: dict[str, object] = {
        "model_name": "text-model",
        "model_vendors": "main-vendor",
        "supports_multimodal": False,
        "vision_model_name": "vision-model",
        "vision_model_vendors": "vision-vendor",
        "vision_system_prompt_path": VISION_SYSTEM_PROMPT_PATH,
        "vision_user_prompt_path": VISION_USER_PROMPT_PATH,
        "group_config": [
            GroupChatConfig(
                group_id="40000",
                system_prompt_path="unused",
                knowledge_base_path="unused",
                max_context_tokens=1000000,
            )
        ],
    }
    values.update(overrides)
    return AIGroupChatConfig.model_validate(values)


def build_message(
    *, message: list[MessageSegment] | None = None, raw_message: str = "你好呀"
) -> GroupMessage:
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
        message=message if message is not None else [Text.new("你好呀")],
        raw_message=raw_message,
        sender=Sender(user_id="20000", nickname="夜袭", role="member"),
    )


def build_builder(
    *,
    database: EmptyDatabase,
    config: AIGroupChatConfig | None = None,
) -> GroupChatMessageBuilder:
    """构造使用固定图片 Bot 的消息输入构造器。"""
    return GroupChatMessageBuilder(
        config=config if config is not None else build_config(),
        database=cast(RedisDatabaseManager, database),
        bot=MissingImageBot(),
        http_client=cast(httpx.AsyncClient, object()),
    )


class GroupChatMessageBuilderTest(unittest.TestCase):
    """验证群消息文本、图片来源顺序和截断规则。"""

    def test_current_message_is_markdown_without_message_id(self) -> None:
        """当前群消息会转成低噪音 Markdown，不暴露消息 ID。"""
        chat_message = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(
                msg=build_message()
            )
        ).turn_messages[0]

        text = chat_message.text or ""
        self.assertIn("## 当前消息", text)
        self.assertIn("- 群: 测试群 (40000)", text)
        self.assertIn("- 群员: 夜袭 (20000, 群员)", text)
        self.assertIn("你好呀", text)
        self.assertNotIn("message_id", text)
        self.assertNotIn("<其他需求>", text)

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

    def test_image_limit_truncates_after_current_then_reply_order(self) -> None:
        """图片上限先保留当前消息图片，再截断引用消息图片。"""
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
                    config=build_config(image_delivery_max_images=2),
                ).build_turn_messages(msg=msg)
            )

        self.assertEqual(result.detected_image_count, 3)
        self.assertEqual(result.truncated_image_count, 1)
        self.assertEqual(
            [artifact.label for artifact in result.image_artifacts],
            ["当前消息第 1 张图片", "当前消息第 2 张图片"],
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

    def test_forward_without_content_keeps_forward_id(self) -> None:
        """没有内嵌内容的合并转发会保留 ID 和工具提示。"""
        msg = build_message(
            message=[Forward.new("forward-empty")],
            raw_message="[合并转发]",
        )

        chat_message = asyncio.run(
            build_builder(database=EmptyDatabase()).build_turn_messages(msg=msg)
        ).turn_messages[0]
        text = chat_message.text or ""

        self.assertIn("合并转发消息，ID: forward-empty", text)
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

        self.assertIn("## 引用消息", text)
        self.assertIn("机器人先前的长回复", text)
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
            build_builder(database=EmptyDatabase()).build_turn_messages(msg=msg)
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
