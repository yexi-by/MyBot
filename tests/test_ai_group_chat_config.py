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

    def test_image_model_can_use_direct_mode_without_vision(self) -> None:
        """主模型支持图片时可明确选择原图直达且不配置视觉模型。"""
        config = build_ai_group_chat_config(supports_images=True)
        self.assertEqual(config.images.delivery_mode, "direct")
        self.assertIsNone(config.vision)

    def test_vision_runtime_fields_are_required(self) -> None:
        """视觉运行参数缺失时直接返回配置字段错误。"""
        config = build_config().model_dump(mode="python")
        vision = config["vision"]
        assert isinstance(vision, dict)
        del vision["max_attempts"]
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            _ = AIGroupChatConfig.model_validate(config)

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

    def test_old_flat_fields_are_rejected(self) -> None:
        """旧模型和视觉字段不作为兼容别名保留。"""
        with self.assertRaises(ValueError):
            _ = build_config(model_name="old-model")


if __name__ == "__main__":
    unittest.main()
