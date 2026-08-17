"""统一配置模型测试。"""

import tomllib
import unittest
from pathlib import Path
from typing import cast

from sqlalchemy import make_url

from app.config import DatabaseConfig, MyBotConfig


def load_example() -> dict[str, object]:
    """读取公开示例配置。"""
    return tomllib.loads(Path("config.example.toml").read_text(encoding="utf-8"))


class SettingsConfigTest(unittest.TestCase):
    """验证统一示例和配置模型保持一致。"""

    def test_example_config_matches_schema(self) -> None:
        """示例配置必须可以被严格模型解析。"""
        config = MyBotConfig.model_validate(load_example())

        self.assertEqual(config.server.port, 6055)
        self.assertEqual(config.napcat.send_max_attempts, 5)
        self.assertEqual(config.database.pool_size, 20)
        self.assertEqual(config.storage.images.download_concurrency, 16)
        self.assertEqual(config.storage.images.max_bytes, 50 * 1024 * 1024)
        self.assertEqual(config.storage.images.retry_delays_seconds, (1, 5, 20))
        self.assertEqual(tuple(config.llm.providers), ("deepseek",))
        self.assertNotIn("firecrawl", config.mcp.servers)
        self.assertIsNotNone(config.plugins.ai_group_chat)
        self.assertIsNotNone(config.plugins.recall_bot_image)

    def test_database_requires_exactly_one_password_source(self) -> None:
        """数据库内联密码和 secret 文件不能同时配置或同时缺失。"""
        raw_config = load_example()
        database = cast(dict[str, object], raw_config["database"])
        database["password_file"] = "/run/secrets/postgres_password"
        with self.assertRaisesRegex(ValueError, "必须且只能配置一个"):
            _ = MyBotConfig.model_validate(raw_config)

        del database["password"]
        config = MyBotConfig.model_validate(raw_config)
        self.assertEqual(
            config.database.password_file,
            "/run/secrets/postgres_password",
        )

    def test_database_rejects_blank_inline_password(self) -> None:
        """内联密码只有空白时在连接数据库前直接报错。"""
        for password in ("", "   "):
            with self.subTest(password_length=len(password)):
                with self.assertRaisesRegex(ValueError, "PostgreSQL 密码不能为空"):
                    _ = DatabaseConfig.model_validate({"password": password})

    def test_database_url_escapes_password_once_for_all_consumers(self) -> None:
        """应用与 migration 共用 URL 构造并保留特殊字符。"""
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

    def test_napcat_send_defaults_when_omitted(self) -> None:
        """发送尝试字段省略时使用积极默认值。"""
        raw_config = load_example()
        napcat = cast(dict[str, object], raw_config["napcat"])
        del napcat["send_max_attempts"]
        del napcat["send_retry_delay_seconds"]

        config = MyBotConfig.model_validate(raw_config)

        self.assertEqual(config.napcat.send_max_attempts, 5)
        self.assertEqual(config.napcat.send_retry_delay_seconds, 0)

    def test_old_config_fields_are_rejected(self) -> None:
        """破坏性配置重构不接受旧字段别名。"""
        raw_config = load_example()
        napcat = cast(dict[str, object], raw_config["napcat"])
        napcat["send_retry_count"] = napcat.pop("send_max_attempts")

        with self.assertRaises(ValueError):
            _ = MyBotConfig.model_validate(raw_config)

    def test_all_plugin_id_lists_reject_duplicates(self) -> None:
        """所有群号和用户 ID 列表都只有一份权威配置。"""
        cases = (
            ("group_notice", "groups"),
            ("auto_unban", "protected_users"),
            ("image_generate", "groups"),
            ("neavo_image_generate", "groups"),
        )
        for section_name, field_name in cases:
            with self.subTest(section=section_name, field=field_name):
                raw_config = load_example()
                plugins = cast(dict[str, object], raw_config["plugins"])
                section = cast(dict[str, object], plugins[section_name])
                section[field_name] = ["123456789", "123456789"]
                with self.assertRaises(ValueError):
                    _ = MyBotConfig.model_validate(raw_config)


if __name__ == "__main__":
    unittest.main()
