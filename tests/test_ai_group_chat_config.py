"""AI 群聊统一配置模型测试。"""

import unittest

from tests.config_helpers import build_ai_group_chat_config


class AIGroupChatConfigTest(unittest.TestCase):
    """验证群配置的唯一性约束。"""

    def test_duplicate_groups_are_rejected(self) -> None:
        """同一个群只能有一份权威配置。"""
        group = {
            "id": "40000",
            "system_prompt_file": "roles/default.md",
            "max_context_tokens": 64000,
        }
        with self.assertRaisesRegex(ValueError, "重复群号"):
            _ = build_ai_group_chat_config(overrides={"groups": [group, group]})


if __name__ == "__main__":
    unittest.main()
