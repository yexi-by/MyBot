"""协议模型边界测试。"""

import unittest

from pydantic import ValidationError

from app.core.event_parser import EventTypeChecker
from app.models import GroupMessage, Image, Sender, StrictModel, Text


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
