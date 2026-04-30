"""AI 群聊插件配置校验测试。"""

import unittest

from app.plugins.ai_group_chat.config import AIGroupChatConfig


class AIGroupChatConfigTest(unittest.TestCase):
    """验证 AI 群聊插件配置的关键约束。"""

    def test_non_multimodal_main_model_requires_fallback_model(self) -> None:
        """主模型不支持多模态时必须配置备用多模态模型。"""
        with self.assertRaisesRegex(ValueError, "multimodal_fallback_model_name"):
            _ = AIGroupChatConfig(
                model_name="deepseek-v4-pro",
                model_vendors="deepseek",
                supports_multimodal=False,
                group_config=[],
            )

    def test_non_multimodal_main_model_accepts_complete_fallback_model(self) -> None:
        """主模型不支持多模态且备用模型完整时配置合法。"""
        config = AIGroupChatConfig(
            model_name="deepseek-v4-pro",
            model_vendors="deepseek",
            supports_multimodal=False,
            multimodal_fallback_model_name="gpt-5.5",
            multimodal_fallback_model_vendors="openai",
            group_config=[],
        )

        self.assertEqual(config.multimodal_fallback_model_name, "gpt-5.5")

    def test_multimodal_main_model_does_not_require_fallback_model(self) -> None:
        """主模型支持多模态时不要求配置备用模型。"""
        config = AIGroupChatConfig(
            model_name="gpt-5.5",
            model_vendors="openai",
            supports_multimodal=True,
            group_config=[],
        )

        self.assertIsNone(config.multimodal_fallback_model_name)
