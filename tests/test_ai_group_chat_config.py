"""AI 群聊统一配置模型测试。"""

import unittest

from app.config import AIGroupChatConfig
from tests.config_helpers import build_ai_group_chat_config


def build_config(**overrides: object) -> AIGroupChatConfig:
    """构造默认使用独立视觉模型的测试配置。"""
    return build_ai_group_chat_config(overrides=overrides)


class AIGroupChatConfigTest(unittest.TestCase):
    """验证模型能力、视觉配置和群配置约束。"""

    def test_text_model_requires_vision(self) -> None:
        """主模型不支持图片时必须提供独立视觉模型。"""
        with self.assertRaisesRegex(ValueError, "必须配置 vision"):
            _ = build_config(vision=None)

    def test_direct_oversize_description_requires_vision(self) -> None:
        """只有显式配置视觉模型时才允许超限图片转描述。"""
        with self.assertRaisesRegex(ValueError, "必须配置 vision"):
            _ = build_ai_group_chat_config(
                supports_images=True,
                overrides={"images": {"oversize_behavior": "describe"}},
            )

    def test_duplicate_groups_are_rejected(self) -> None:
        """同一个群只能有一份权威配置。"""
        group = {
            "id": "40000",
            "system_prompt_file": "roles/default.md",
            "max_context_tokens": 64000,
        }
        with self.assertRaisesRegex(ValueError, "重复群号"):
            _ = build_config(groups=[group, group])


if __name__ == "__main__":
    unittest.main()
