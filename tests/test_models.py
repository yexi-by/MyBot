"""协议模型边界测试。"""

import unittest

from pydantic import ValidationError

from app.models import Sender


class StrictModelBoundaryTest(unittest.TestCase):
    """验证协议模型默认拒绝未知字段。"""

    def test_sender_rejects_unknown_field(self) -> None:
        """NapCat 事件模型不会静默吞掉未知字段。"""
        with self.assertRaises(ValidationError):
            Sender.model_validate(
                {
                    "user_id": "10000",
                    "nickname": "夜袭",
                    "unexpected": "不应该被吞掉",
                }
            )
