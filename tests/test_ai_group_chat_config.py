"""AI 群聊插件配置校验测试。"""

import unittest

from app.plugins.ai_group_chat.config import AIGroupChatConfig

VISION_SYSTEM_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/system.md"
VISION_USER_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/user.md"


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
            tool_image_observation_system_prompt_path=VISION_SYSTEM_PROMPT_PATH,
            tool_image_observation_user_prompt_path=VISION_USER_PROMPT_PATH,
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

    def test_forward_image_tool_defaults_are_valid(self) -> None:
        """合并转发图片工具数量配置有明确边界。"""
        config = AIGroupChatConfig(
            model_name="deepseek-v4-pro",
            model_vendors="deepseek",
            supports_multimodal=False,
            multimodal_fallback_model_name="gpt-5.5-vision",
            multimodal_fallback_model_vendors="openai",
            tool_image_observation_system_prompt_path=VISION_SYSTEM_PROMPT_PATH,
            tool_image_observation_user_prompt_path=VISION_USER_PROMPT_PATH,
            group_config=[],
        )

        self.assertTrue(config.forward_image_tool_enabled)
        self.assertEqual(config.forward_image_max_images_per_call, 6)
        self.assertEqual(config.forward_image_max_all_images, 12)
        self.assertEqual(config.forward_image_fetch_concurrency, 4)
        self.assertEqual(config.forward_image_download_timeout_seconds, 15.0)
        self.assertEqual(config.max_reply_chars, 100)
        self.assertEqual(config.tool_image_delivery_mode, "auto")
        self.assertEqual(config.tool_image_summary_max_images, 6)
        self.assertFalse(config.persist_tool_image_observations)
        self.assertEqual(
            config.tool_image_observation_system_prompt_path,
            VISION_SYSTEM_PROMPT_PATH,
        )
        self.assertEqual(
            config.tool_image_observation_user_prompt_path,
            VISION_USER_PROMPT_PATH,
        )

    def test_forward_image_limits_reject_invalid_values(self) -> None:
        """合并转发图片工具数量和并发配置必须有明确边界。"""
        with self.assertRaises(ValueError):
            _ = AIGroupChatConfig(
                model_name="deepseek-v4-pro",
                model_vendors="deepseek",
                supports_multimodal=False,
                multimodal_fallback_model_name="gpt-5.5-vision",
                multimodal_fallback_model_vendors="openai",
                tool_image_observation_system_prompt_path=VISION_SYSTEM_PROMPT_PATH,
                tool_image_observation_user_prompt_path=VISION_USER_PROMPT_PATH,
                forward_image_max_images_per_call=0,
                group_config=[],
            )

    def test_observation_prompt_requires_explicit_files_for_summary(self) -> None:
        """需要视觉摘要时，必须显式配置两个提示词文件路径。"""
        with self.assertRaisesRegex(
            ValueError, "tool_image_observation_system_prompt_path"
        ):
            _ = AIGroupChatConfig(
                model_name="deepseek-v4-pro",
                model_vendors="deepseek",
                supports_multimodal=False,
                multimodal_fallback_model_name="gpt-5.5-vision",
                multimodal_fallback_model_vendors="openai",
                group_config=[],
            )

    def test_max_reply_chars_rejects_invalid_values(self) -> None:
        """AI 群回复普通发送字数阈值必须大于零。"""
        with self.assertRaises(ValueError):
            _ = AIGroupChatConfig(
                model_name="deepseek-v4-pro",
                model_vendors="deepseek",
                supports_multimodal=False,
                multimodal_fallback_model_name="gpt-5.5-vision",
                multimodal_fallback_model_vendors="openai",
                tool_image_observation_system_prompt_path=VISION_SYSTEM_PROMPT_PATH,
                tool_image_observation_user_prompt_path=VISION_USER_PROMPT_PATH,
                max_reply_chars=0,
                group_config=[],
            )

    def test_inline_observation_prompt_fields_are_rejected(self) -> None:
        """视觉摘要提示词不允许通过内联配置绕过文件边界。"""
        with self.assertRaisesRegex(ValueError, "tool_image_observation_system_prompt"):
            _ = AIGroupChatConfig.model_validate(
                {
                    "model_name": "deepseek-v4-pro",
                    "model_vendors": "deepseek",
                    "supports_multimodal": False,
                    "multimodal_fallback_model_name": "gpt-5.5-vision",
                    "multimodal_fallback_model_vendors": "openai",
                    "tool_image_observation_system_prompt_path": (
                        VISION_SYSTEM_PROMPT_PATH
                    ),
                    "tool_image_observation_user_prompt_path": VISION_USER_PROMPT_PATH,
                    "tool_image_observation_system_prompt": "禁止内联",
                    "group_config": [],
                }
            )
