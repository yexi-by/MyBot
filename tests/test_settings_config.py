"""全局配置模型测试。"""

import tomllib
import unittest
from pathlib import Path

from app.config import Settings


class SettingsConfigTest(unittest.TestCase):
    """验证 setting.toml 示例和配置模型保持同步。"""

    def test_example_setting_matches_schema(self) -> None:
        """示例配置必须可以被严格配置模型解析。"""
        raw_config = tomllib.loads(
            Path("setting.example.toml").read_text(encoding="utf-8")
        )
        settings = Settings.model_validate(raw_config)

        self.assertEqual(settings.server.port, 6055)
        self.assertEqual(settings.server.websocket_path_prefix, "/ws")
        self.assertEqual(len(settings.llm.providers), 1)
