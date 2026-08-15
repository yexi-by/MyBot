"""全局配置模型测试。"""

import tomllib
import unittest
from pathlib import Path

from sqlalchemy import make_url

from app.config import DatabaseConfig, Settings


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
        self.assertEqual(settings.database.port, 5432)
        self.assertEqual(settings.database.pool_size, 10)
        self.assertEqual(settings.storage.image_max_bytes, 50 * 1024 * 1024)
        self.assertEqual(settings.storage.image_retry_delays_seconds, (5, 30, 300))
        self.assertEqual(len(settings.llm.providers), 1)
        self.assertNotIn("firecrawl", settings.mcp.mcpServers)

    def test_database_requires_exactly_one_password_source(self) -> None:
        """数据库明文密码和 secret 文件不能同时配置或同时缺失。"""
        raw_config = tomllib.loads(
            Path("setting.example.toml").read_text(encoding="utf-8")
        )
        raw_config["database"]["password_file"] = "/run/secrets/postgres_password"
        with self.assertRaisesRegex(ValueError, "必须且只能配置一个"):
            _ = Settings.model_validate(raw_config)

        del raw_config["database"]["password"]
        settings = Settings.model_validate(raw_config)
        self.assertEqual(
            settings.database.password_file, "/run/secrets/postgres_password"
        )

    def test_database_rejects_blank_inline_password(self) -> None:
        """明文密码只有空白时在连接数据库前直接报配置错误。"""
        for password in ("", "   "):
            with self.subTest(password_length=len(password)):
                with self.assertRaisesRegex(ValueError, "PostgreSQL 密码不能为空"):
                    _ = DatabaseConfig.model_validate({"password": password})

    def test_database_url_escapes_password_once_for_all_consumers(self) -> None:
        """应用与 migration 共用的 URL 构造必须保留特殊字符密码。"""
        config = DatabaseConfig.model_validate(
            {
                "host": "postgres",
                "name": "mybot",
                "user": "mybot",
                "password": "p@ss:/%word",
            }
        )

        parsed = make_url(config.build_url())

        self.assertEqual(parsed.drivername, "postgresql+asyncpg")
        self.assertEqual(parsed.host, "postgres")
        self.assertEqual(parsed.password, "p@ss:/%word")

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
