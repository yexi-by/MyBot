"""DeepSeek V4 Depth 0 提示词加载测试。"""

import tempfile
import unittest
from pathlib import Path

from app.plugins.ai_group_chat.config import AIGroupChatConfig
from app.plugins.ai_group_chat.deepseek_v4_prompt import load_deepseek_v4_prompt_pack


def build_config(
    *, extra_requirements_path: Path, roleplay_instruct_path: Path
) -> AIGroupChatConfig:
    """构造带 DeepSeek V4 提示词路径的测试配置。"""
    return AIGroupChatConfig(
        model_name="deepseek-v4-pro",
        model_vendors="CLIProxyAPI",
        enable_deepseek_v4_roleplay_instruct=True,
        deepseek_v4_extra_requirements_path=str(extra_requirements_path),
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
