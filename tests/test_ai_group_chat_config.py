"""AI 群聊统一配置模型测试。"""

import unittest

from app.config import AIGroupChatConfig


def build_config(**overrides: object) -> AIGroupChatConfig:
    """构造默认使用独立视觉模型的测试配置。"""
    values: dict[str, object] = {
        "model": {
            "provider": "main-provider",
            "name": "main-model",
            "supports_images": False,
        },
        "vision": {
            "model": {"provider": "vision-provider", "name": "vision-model"},
            "system_prompt_file": "vision/system.md",
            "user_prompt_file": "vision/user.md",
        },
        "groups": [],
    }
    values.update(overrides)
    return AIGroupChatConfig.model_validate(values)


class AIGroupChatConfigTest(unittest.TestCase):
    """验证模型能力、视觉配置和群配置约束。"""

    def test_text_model_requires_vision(self) -> None:
        """主模型不支持图片时必须提供独立视觉模型。"""
        with self.assertRaisesRegex(ValueError, "必须配置 vision"):
            _ = AIGroupChatConfig.model_validate(
                {
                    "model": {
                        "provider": "main-provider",
                        "name": "main-model",
                        "supports_images": False,
                    }
                }
            )

    def test_image_model_forbids_vision(self) -> None:
        """主模型支持图片时不保留不会消费的视觉配置。"""
        with self.assertRaisesRegex(ValueError, "不能配置 vision"):
            _ = build_config(
                model={
                    "provider": "main-provider",
                    "name": "main-model",
                    "supports_images": True,
                }
            )

    def test_vision_retry_defaults(self) -> None:
        """视觉请求默认最多尝试五次并使用短退避。"""
        config = build_config()

        self.assertIsNotNone(config.vision)
        assert config.vision is not None
        self.assertEqual(config.vision.max_attempts, 5)
        self.assertEqual(config.vision.retry_delay_seconds, 0.25)

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
