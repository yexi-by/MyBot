"""AI 群聊插件配置与通用 system prompt 测试。"""

import tempfile
import unittest
from pathlib import Path

from app.plugins.ai_group_chat.config import (
    AIGroupChatConfig,
    GroupChatConfig,
    build_system_prompt,
)

VISION_SYSTEM_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/system.md"
VISION_USER_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/user.md"


def build_text_model_config(**overrides: object) -> AIGroupChatConfig:
    """构造使用独立视觉工具的测试配置。"""
    values: dict[str, object] = {
        "model_name": "text-model",
        "model_vendors": "text-vendor",
        "supports_multimodal": False,
        "vision_model_name": "vision-model",
        "vision_model_vendors": "vision-vendor",
        "vision_system_prompt_path": VISION_SYSTEM_PROMPT_PATH,
        "vision_user_prompt_path": VISION_USER_PROMPT_PATH,
        "group_config": [],
    }
    values.update(overrides)
    return AIGroupChatConfig.model_validate(values)


class AIGroupChatConfigTest(unittest.TestCase):
    """验证主模型能力、视觉配置和通用要求的关键约束。"""

    def test_non_multimodal_main_model_requires_complete_vision_config(self) -> None:
        """主模型不支持多模态时，四个视觉字段都必须填写。"""
        with self.assertRaisesRegex(ValueError, "vision_model_name"):
            _ = AIGroupChatConfig(
                model_name="text-model",
                model_vendors="text-vendor",
                supports_multimodal=False,
                group_config=[],
            )

    def test_non_multimodal_main_model_accepts_complete_vision_config(self) -> None:
        """独立视觉模型和两个提示词文件完整时配置合法。"""
        config = build_text_model_config()

        self.assertEqual(config.vision_model_name, "vision-model")
        self.assertEqual(config.vision_model_vendors, "vision-vendor")

    def test_multimodal_main_model_rejects_unused_vision_config(self) -> None:
        """主模型可直接看图时，禁止填写不会被消费的视觉字段。"""
        with self.assertRaisesRegex(ValueError, "vision_model_name"):
            _ = AIGroupChatConfig(
                model_name="multimodal-model",
                model_vendors="main-vendor",
                supports_multimodal=True,
                vision_model_name="unused-model",
                group_config=[],
            )

    def test_multimodal_main_model_needs_no_vision_config(self) -> None:
        """主模型支持多模态时，只配置主模型即可。"""
        config = AIGroupChatConfig(
            model_name="multimodal-model",
            model_vendors="main-vendor",
            supports_multimodal=True,
            group_config=[],
        )

        self.assertIsNone(config.vision_model_name)

    def test_image_and_forward_defaults_are_valid(self) -> None:
        """共用图片读取、单轮上限和合并转发配置有明确默认值。"""
        config = build_text_model_config()

        self.assertEqual(config.image_delivery_max_images, 6)
        self.assertEqual(config.image_fetch_concurrency, 4)
        self.assertEqual(config.image_download_timeout_seconds, 15.0)
        self.assertEqual(config.vision_request_retry_count, 3)
        self.assertEqual(config.vision_request_retry_delay_seconds, 1.0)
        self.assertTrue(config.persist_vision_descriptions)
        self.assertTrue(config.forward_image_tool_enabled)
        self.assertEqual(config.forward_image_max_images_per_call, 6)
        self.assertEqual(config.forward_image_max_all_images, 12)
        self.assertEqual(config.max_reply_chars, 100)
        self.assertFalse(config.output_reasoning_content)

    def test_image_limits_reject_invalid_values(self) -> None:
        """图片数量、并发和超时必须在配置边界内。"""
        for field_name, value in (
            ("image_delivery_max_images", 0),
            ("image_fetch_concurrency", 0),
            ("image_download_timeout_seconds", 0),
            ("vision_request_retry_count", 0),
            ("vision_request_retry_delay_seconds", 0),
            ("forward_image_max_images_per_call", 0),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValueError):
                    _ = build_text_model_config(**{field_name: value})

    def test_multimodal_main_model_rejects_explicit_vision_retry_config(
        self,
    ) -> None:
        """主模型直接看图时，禁止填写不会执行的视觉请求重试参数。"""
        with self.assertRaisesRegex(ValueError, "vision_request_retry_count"):
            _ = AIGroupChatConfig(
                model_name="multimodal-model",
                model_vendors="main-vendor",
                supports_multimodal=True,
                vision_request_retry_count=3,
                group_config=[],
            )

    def test_vision_prompt_files_must_exist_and_be_nonempty(self) -> None:
        """视觉工具启用时，两个提示词路径必须指向非空文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            empty_path = Path(temp_dir) / "empty.md"
            empty_path.write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "vision_system_prompt_path"):
                _ = build_text_model_config(
                    vision_system_prompt_path=str(empty_path)
                )

    def test_removed_fields_are_rejected(self) -> None:
        """旧备用模型、图片观察和 DSV4 字段不保留兼容入口。"""
        removed_fields = (
            "multimodal_fallback_model_name",
            "multimodal_fallback_model_vendors",
            "tool_image_delivery_mode",
            "tool_image_observation_system_prompt_path",
            "tool_image_observation_user_prompt_path",
            "tool_image_summary_max_images",
            "persist_tool_image_observations",
            "forward_image_fetch_concurrency",
            "forward_image_download_timeout_seconds",
            "enable_deepseek_v4_roleplay_instruct",
            "deepseek_v4_roleplay_instruct_path",
        )
        for field_name in removed_fields:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    _ = build_text_model_config(**{field_name: "removed"})

    def test_system_prompt_always_includes_generic_requirements(self) -> None:
        """角色、知识库和通用群聊要求始终一起进入 system。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            system_path = root / "system.md"
            knowledge_path = root / "knowledge.md"
            extra_path = root / "extra.md"
            system_path.write_text("角色提示", encoding="utf-8")
            knowledge_path.write_text("群知识库", encoding="utf-8")
            extra_path.write_text("<Reply> 与 <At> 用法", encoding="utf-8")
            config = AIGroupChatConfig(
                model_name="any-model-name",
                model_vendors="main-vendor",
                supports_multimodal=True,
                extra_requirements_path=str(extra_path),
                group_config=[],
            )
            group_config = GroupChatConfig(
                group_id="10000",
                system_prompt_path=str(system_path),
                knowledge_base_path=str(knowledge_path),
                max_context_tokens=1000000,
            )

            result = build_system_prompt(config=config, group_config=group_config)

            self.assertEqual(result, "角色提示\n\n群知识库\n\n<Reply> 与 <At> 用法")


if __name__ == "__main__":
    unittest.main()
