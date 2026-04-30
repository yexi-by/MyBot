"""协议模型边界测试。"""

import unittest

from pydantic import ValidationError

from app.core.event_parser import EventTypeChecker
from app.models import (
    GroupMessage,
    Image,
    NotifyEvent,
    Sender,
    StrictModel,
    Text,
    UnknownSegment,
)


class StrictModelBoundaryTest(unittest.TestCase):
    """验证内部模型和 NapCat 入站模型的边界策略。"""

    def test_internal_strict_model_rejects_unknown_field(self) -> None:
        """内部严格模型仍然拒绝未知字段。"""

        class DemoStrictModel(StrictModel):
            """测试用严格模型。"""

            name: str

        with self.assertRaises(ValidationError):
            DemoStrictModel.model_validate({"name": "夜袭", "unexpected": "拒绝"})

    def test_napcat_sender_ignores_unknown_field(self) -> None:
        """NapCat 入站模型忽略未消费的上游扩展字段。"""
        sender = Sender.model_validate(
            {
                "user_id": "10000",
                "nickname": "夜袭",
                "unexpected": "上游扩展字段",
            }
        )
        self.assertEqual(sender.user_id, "10000")
        self.assertNotIn("unexpected", sender.model_dump())

    def test_group_message_parser_accepts_extra_napcat_fields(self) -> None:
        """事件解析器不会因为 NapCat 额外字段丢弃群消息。"""
        event = EventTypeChecker().get_event(
            {
                "time": 1710000000,
                "self_id": 742654932,
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "user_id": 2172959822,
                "message_id": 123456,
                "group_id": 939506743,
                "message": [
                    {"type": "at", "data": {"qq": 742654932, "extra": "忽略"}},
                    {"type": "text", "data": {"text": " 你好", "extra": "忽略"}},
                ],
                "raw_message": "@SMartBOT三国 你好",
                "sender": {
                    "user_id": 2172959822,
                    "nickname": "夜袭",
                    "card": "",
                    "role": "owner",
                    "shut_up_timestamp": 0,
                },
                "message_format": "array",
                "raw": {"上游": "额外字段"},
                "上游新增字段": "忽略",
            }
        )
        self.assertIsInstance(event, GroupMessage)
        assert isinstance(event, GroupMessage)
        self.assertEqual(event.group_id, "939506743")
        self.assertIsInstance(event.message[1], Text)

    def test_image_sub_type_accepts_integer(self) -> None:
        """图片消息段的 sub_type 支持 NapCat 上报整数并统一收敛为字符串。"""
        event = EventTypeChecker().get_event(
            {
                "time": 1710000000,
                "self_id": 742654932,
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "user_id": 2172959822,
                "message_id": 123456,
                "group_id": 939506743,
                "message": [
                    {
                        "type": "image",
                        "data": {
                            "file": "abc.image",
                            "url": "https://example.com/a.png",
                            "sub_type": 0,
                        },
                    }
                ],
                "raw_message": "[图片]",
                "sender": {"user_id": 2172959822, "nickname": "夜袭"},
                "message_format": "array",
            }
        )
        self.assertIsInstance(event, GroupMessage)
        assert isinstance(event, GroupMessage)
        self.assertIsInstance(event.message[0], Image)
        assert isinstance(event.message[0], Image)
        self.assertEqual(event.message[0].data.sub_type, "0")

    def test_napcat_model_coerces_numeric_strings(self) -> None:
        """NapCat 入站字符串字段遇到数字时会统一收敛为字符串。"""
        event = EventTypeChecker().get_event(
            {
                "time": 1710000000,
                "self_id": 742654932,
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "user_id": 2172959822,
                "message_id": 123456,
                "group_id": 939506743,
                "group_name": 123,
                "message": [{"type": "text", "data": {"text": 456}}],
                "raw_message": 456,
                "sender": {"user_id": 2172959822, "nickname": 789, "card": 100},
                "message_format": "array",
            }
        )
        self.assertIsInstance(event, GroupMessage)
        assert isinstance(event, GroupMessage)
        self.assertEqual(event.group_name, "123")
        self.assertEqual(event.raw_message, "456")
        self.assertEqual(event.sender.nickname, "789")
        self.assertEqual(event.sender.card, "100")
        self.assertIsInstance(event.message[0], Text)
        assert isinstance(event.message[0], Text)
        self.assertEqual(event.message[0].data.text, "456")

    def test_string_format_message_is_converted_to_text_segment(self) -> None:
        """NapCat string 格式消息会转为 text 消息段。"""
        event = EventTypeChecker().get_event(
            {
                "time": 1710000000,
                "self_id": 742654932,
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "user_id": 2172959822,
                "message_id": 123456,
                "group_id": 939506743,
                "message": "纯文本消息",
                "raw_message": "纯文本消息",
                "sender": {"user_id": 2172959822, "nickname": "夜袭"},
                "message_format": "string",
            }
        )
        self.assertIsInstance(event, GroupMessage)
        assert isinstance(event, GroupMessage)
        self.assertIsInstance(event.message[0], Text)
        assert isinstance(event.message[0], Text)
        self.assertEqual(event.message[0].data.text, "纯文本消息")

    def test_unknown_segment_does_not_drop_whole_message(self) -> None:
        """未知消息段解析为 UnknownSegment，整条群消息不中断。"""
        event = EventTypeChecker().get_event(
            {
                "time": 1710000000,
                "self_id": 742654932,
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "user_id": 2172959822,
                "message_id": 123456,
                "group_id": 939506743,
                "message": [{"type": "new_upstream_type", "data": {"value": 1}}],
                "raw_message": "[新消息段]",
                "sender": {"user_id": 2172959822, "nickname": "夜袭"},
                "message_format": "array",
            }
        )
        self.assertIsInstance(event, GroupMessage)
        assert isinstance(event, GroupMessage)
        self.assertIsInstance(event.message[0], UnknownSegment)
        assert isinstance(event.message[0], UnknownSegment)
        self.assertEqual(event.message[0].type, "new_upstream_type")

    def test_notify_raw_info_accepts_non_object_json(self) -> None:
        """notify raw_info 可接受上游任意 JSON 结构。"""
        event = EventTypeChecker().get_event(
            {
                "time": 1710000000,
                "self_id": 742654932,
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "group_id": 939506743,
                "user_id": 2172959822,
                "target_id": 742654932,
                "raw_info": [{"label": "上游数组"}],
            }
        )
        self.assertIsInstance(event, NotifyEvent)
        assert isinstance(event, NotifyEvent)
        self.assertEqual(event.raw_info, [{"label": "上游数组"}])
