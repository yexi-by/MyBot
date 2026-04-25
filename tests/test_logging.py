"""日志门面测试。"""

import unittest

from app.utils.log import format_log_fields, log_event


class LoggingFacadeTest(unittest.TestCase):
    """验证结构化日志门面的纯函数部分。"""

    def test_format_log_fields_is_stable(self) -> None:
        """结构化字段会渲染为稳定后缀。"""
        self.assertEqual(
            format_log_fields({"event_id": "abc", "count": 2}),
            " | event_id=abc count=2",
        )

    def test_log_event_accepts_structured_fields(self) -> None:
        """事件日志入口可以接收结构化字段。"""
        log_event(
            level="DEBUG",
            event="test.logging",
            category="test",
            message="测试日志",
            count=1,
        )
