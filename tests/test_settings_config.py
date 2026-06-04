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
        self.assertEqual(settings.napcat.send_retry_count, 3)
        self.assertEqual(settings.napcat.send_retry_delay, 1)
        self.assertEqual(len(settings.llm.providers), 1)
        self.assertNotIn("firecrawl", settings.mcp.mcpServers)

    def test_napcat_send_retry_defaults_when_omitted(self) -> None:
        """旧配置缺少 NapCat 发送重试字段时使用默认值。"""
        raw_config = tomllib.loads(
            Path("setting.example.toml").read_text(encoding="utf-8")
        )
        del raw_config["napcat"]["send_retry_count"]
        del raw_config["napcat"]["send_retry_delay"]

        settings = Settings.model_validate(raw_config)

        self.assertEqual(settings.napcat.send_retry_count, 3)
        self.assertEqual(settings.napcat.send_retry_delay, 1)
