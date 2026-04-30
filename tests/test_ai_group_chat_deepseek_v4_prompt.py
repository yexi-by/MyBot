"""DeepSeek V4 Depth 0 提示词加载测试。"""

import tempfile
import unittest
from pathlib import Path

from app.plugins.ai_group_chat.config import (
    AIGroupChatConfig,
    GroupChatConfig,
    build_system_prompt,
)
from app.plugins.ai_group_chat.deepseek_v4_prompt import load_deepseek_v4_prompt_pack


def build_config(
    *, extra_requirements_path: Path, roleplay_instruct_path: Path
) -> AIGroupChatConfig:
    """构造带 DeepSeek V4 提示词路径的测试配置。"""
    return AIGroupChatConfig(
        model_name="deepseek-v4-pro",
        model_vendors="CLIProxyAPI",
        multimodal_fallback_model_name="gpt-5.5-vision",
        multimodal_fallback_model_vendors="CLIProxyAPI",
        enable_deepseek_v4_roleplay_instruct=True,
        extra_requirements_path=str(extra_requirements_path),
        deepseek_v4_roleplay_instruct_path=str(roleplay_instruct_path),
        group_config=[],
    )


class DeepSeekV4PromptPackTest(unittest.TestCase):
    """验证 DeepSeek V4 Depth 0 提示词文件配置。"""

    def test_prompt_pack_loads_configured_files(self) -> None:
        """配置的两个 Markdown 文件会被合并为 Depth 0 user message。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extra_path = root / "extra.md"
            roleplay_path = root / "roleplay.md"
            extra_path.write_text("动作偏好", encoding="utf-8")
            roleplay_path.write_text("角色沉浸", encoding="utf-8")
            config = build_config(
                extra_requirements_path=extra_path,
                roleplay_instruct_path=roleplay_path,
            )

            prompt_pack = load_deepseek_v4_prompt_pack(config=config)
            message = prompt_pack.build_depth_zero_message()

            self.assertEqual(message.role, "user")
            text = message.text or ""
            self.assertIn("<其他需求>", text)
            self.assertIn("动作偏好", text)
            self.assertIn("<角色沉浸式扮演需求>", text)
            self.assertIn("角色沉浸", text)

    def test_prompt_pack_can_skip_extra_requirements(self) -> None:
        """system 已携带通用要求时，Depth 0 可只包含角色沉浸提示。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extra_path = root / "extra.md"
            roleplay_path = root / "roleplay.md"
            extra_path.write_text("动作偏好", encoding="utf-8")
            roleplay_path.write_text("角色沉浸", encoding="utf-8")
            config = build_config(
                extra_requirements_path=extra_path,
                roleplay_instruct_path=roleplay_path,
            )

            prompt_pack = load_deepseek_v4_prompt_pack(config=config)
            message = prompt_pack.build_depth_zero_message(
                include_extra_requirements=False
            )

            text = message.text or ""
            self.assertNotIn("<其他需求>", text)
            self.assertIn("<角色沉浸式扮演需求>", text)

    def test_missing_prompt_file_fails_fast_with_path(self) -> None:
        """提示词文件缺失时直接报出缺失路径。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extra_path = root / "missing-extra.md"
            roleplay_path = root / "roleplay.md"
            roleplay_path.write_text("角色沉浸", encoding="utf-8")
            config = build_config(
                extra_requirements_path=extra_path,
                roleplay_instruct_path=roleplay_path,
            )

            with self.assertRaisesRegex(FileNotFoundError, "missing-extra.md"):
                _ = load_deepseek_v4_prompt_pack(config=config)

    def test_non_deepseek_v4_system_prompt_includes_extra_requirements(self) -> None:
        """非 DeepSeek V4 模型会把通用要求拼进 system，方便学习 Reply/At 标记。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            system_path = root / "system.md"
            knowledge_path = root / "knowledge.md"
            extra_path = root / "extra.md"
            roleplay_path = root / "roleplay.md"
            system_path.write_text("你是猫娘。", encoding="utf-8")
            knowledge_path.write_text("群知识库。", encoding="utf-8")
            extra_path.write_text("<Reply> 和 <At> 的用法", encoding="utf-8")
            roleplay_path.write_text("角色沉浸", encoding="utf-8")
            config = AIGroupChatConfig(
                model_name="gpt-5.5",
                model_vendors="CLIProxyAPI",
                multimodal_fallback_model_name="gpt-5.5-vision",
                multimodal_fallback_model_vendors="CLIProxyAPI",
                enable_deepseek_v4_roleplay_instruct=True,
                extra_requirements_path=str(extra_path),
                deepseek_v4_roleplay_instruct_path=str(roleplay_path),
                group_config=[],
            )
            group_config = GroupChatConfig(
                group_id="10000",
                system_prompt_path=str(system_path),
                knowledge_base_path=str(knowledge_path),
                max_context_tokens=1000000,
            )

            system_prompt = build_system_prompt(
                config=config,
                group_config=group_config,
            )

            self.assertIn("你是猫娘。", system_prompt)
            self.assertIn("群知识库。", system_prompt)
            self.assertIn("<Reply> 和 <At> 的用法", system_prompt)
            self.assertNotIn("角色沉浸", system_prompt)

    def test_deepseek_v4_system_prompt_keeps_extra_requirements_out(self) -> None:
        """DeepSeek V4 开启专属策略时，通用要求只进入 Depth 0，不污染长期 system。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            system_path = root / "system.md"
            knowledge_path = root / "knowledge.md"
            extra_path = root / "extra.md"
            roleplay_path = root / "roleplay.md"
            system_path.write_text("你是猫娘。", encoding="utf-8")
            knowledge_path.write_text("群知识库。", encoding="utf-8")
            extra_path.write_text("<Reply> 和 <At> 的用法", encoding="utf-8")
            roleplay_path.write_text("角色沉浸", encoding="utf-8")
            config = build_config(
                extra_requirements_path=extra_path,
                roleplay_instruct_path=roleplay_path,
            )
            group_config = GroupChatConfig(
                group_id="10000",
                system_prompt_path=str(system_path),
                knowledge_base_path=str(knowledge_path),
                max_context_tokens=1000000,
            )

            system_prompt = build_system_prompt(
                config=config,
                group_config=group_config,
            )

            self.assertIn("你是猫娘。", system_prompt)
            self.assertIn("群知识库。", system_prompt)
            self.assertNotIn("<Reply> 和 <At> 的用法", system_prompt)
            self.assertNotIn("角色沉浸", system_prompt)
