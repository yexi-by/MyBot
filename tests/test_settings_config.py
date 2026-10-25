"""统一配置模型测试。"""

import shutil
import tomllib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from sqlalchemy import make_url

from app.config import (
    ConfigManager,
    DatabaseConfig,
    LLMProviderConfig,
    MCPServerConfig,
    MyBotConfig,
    NapCatConfig,
    PluginExecutionConfig,
    load_config,
)
from app.core.di import MyProvider


def load_example() -> dict[str, object]:
    """读取公开示例配置。"""
    return tomllib.loads(Path("config.example.toml").read_text(encoding="utf-8"))


class SettingsConfigTest(unittest.TestCase):
    """验证统一示例和配置模型保持一致。"""

    def test_example_config_loads_with_default_prompts(self) -> None:
        """示例配置连同 config.example/ 默认提示词复制到 config/ 后即可加载。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_root = Path(temp_dir)
            _ = shutil.copytree("config.example", config_root, dirs_exist_ok=True)
            config_file = config_root / "mybot.toml"
            _ = shutil.copyfile("config.example.toml", config_file)

            loaded = load_config(config_file=config_file)

        self.assertIsNotNone(loaded.plugins.ai_group_chat)

    def test_mcp_rejects_mixed_or_missing_transport_targets(self) -> None:
        """连接字段不能被选错传输后静默忽略，缺少目标也不能保存。"""
        for server in (
            {"transport": "streamable_http", "url": "https://example.com/mcp", "command": "npx"},
            {"transport": "streamable_http", "url": "https://example.com/mcp", "env": {"API_KEY": "test"}},
            {"transport": "stdio", "command": "npx", "headers": {"Authorization": "Bearer test"}},
            {"transport": "streamable_http"},
            {"transport": "stdio"},
        ):
            with self.subTest(server=server), self.assertRaises(ValueError):
                _ = MCPServerConfig.model_validate({**server, "disabled": False})

    def test_database_rejects_two_password_sources(self) -> None:
        """数据库内联密码和 secret 文件不能同时配置。"""
        raw_config = load_example()
        database = cast(dict[str, object], raw_config["database"])
        database["password_file"] = "/run/secrets/postgres_password"
        with self.assertRaisesRegex(ValueError, "不能同时配置"):
            _ = MyBotConfig.model_validate(raw_config)

        del database["password"]
        config = MyBotConfig.model_validate(raw_config)
        self.assertEqual(
            config.database.password_file,
            "/run/secrets/postgres_password",
        )

    def test_database_allows_passwordless_connection(self) -> None:
        """缺少密码或输入空白密码时构造无密码 PostgreSQL URL。"""
        required = {
            "host": "localhost",
            "port": 5432,
            "name": "mybot",
            "user": "mybot",
            "pool_size": 20,
            "max_overflow": 20,
            "pool_timeout_seconds": 2,
            "statement_timeout_seconds": 5,
            "persistence_retry_delays_seconds": [0.25],
        }
        for password in ("", "   "):
            with self.subTest(password_length=len(password)):
                config = DatabaseConfig.model_validate(
                    {**required, "password": password}
                )
                parsed = make_url(config.build_url())

                self.assertIsNone(config.password)
                self.assertIsNone(parsed.password)

        config = DatabaseConfig.model_validate(required)
        self.assertIsNone(config.resolve_password())
        self.assertIsNone(make_url(config.build_url()).password)

        with tempfile.TemporaryDirectory() as temp_dir:
            password_file = Path(temp_dir) / "password"
            password_file.write_text("\n", encoding="utf-8")
            file_config = DatabaseConfig.model_validate(
                {**required, "password_file": str(password_file)}
            )
            self.assertIsNone(file_config.resolve_password())
            self.assertIsNone(make_url(file_config.build_url()).password)

    def test_database_url_escapes_password_once_for_all_consumers(self) -> None:
        """应用与 migration 共用 URL 构造并保留特殊字符。"""
        config = DatabaseConfig.model_validate(
            {
                "host": "postgres",
                "name": "mybot",
                "user": "mybot",
                "password": "p@ss:/%word",
                "port": 5432,
                "pool_size": 20,
                "max_overflow": 20,
                "pool_timeout_seconds": 2,
                "statement_timeout_seconds": 5,
                "persistence_retry_delays_seconds": [0.25],
            }
        )

        parsed = make_url(config.build_url())

        self.assertEqual(parsed.drivername, "postgresql+asyncpg")
        self.assertEqual(parsed.host, "postgres")
        self.assertEqual(parsed.password, "p@ss:/%word")

    def test_service_authentication_values_are_optional(self) -> None:
        """NapCat 和 LLM 服务均可明确选择无鉴权运行。"""
        napcat = NapCatConfig.model_validate(
            {
                "websocket_token": "  ",
                "action_timeout_seconds": 120,
                "send_max_attempts": 5,
                "send_retry_delay_seconds": 0,
                "send_retry_max_delay_seconds": 10,
                "response_summary_max_chars": 500,
            }
        )
        provider = LLMProviderConfig.model_validate(
            {
                "api_format": "chat_completions",
                "api_key": "",
                "inherit_network_proxy": True,
                "timeout_seconds": 0,
                "max_attempts": 5,
                "retry_delay_seconds": 0,
                "retry_max_delay_seconds": 10,
            }
        )

        self.assertIsNone(napcat.websocket_token)
        self.assertIsNone(provider.api_key)

    def test_all_plugin_id_lists_reject_duplicates(self) -> None:
        """所有群号和用户 ID 列表都只有一份权威配置。"""
        cases = (
            ("group_notice", "groups"),
            ("auto_unban", "protected_users"),
        )
        for section_name, field_name in cases:
            with self.subTest(section=section_name, field=field_name):
                raw_config = load_example()
                plugins = cast(dict[str, object], raw_config["plugins"])
                section = cast(dict[str, object], plugins[section_name])
                section[field_name] = ["123456789", "123456789"]
                with self.assertRaises(ValueError):
                    _ = MyBotConfig.model_validate(raw_config)

    def test_plugin_execution_accepts_dynamic_plugin_ids(self) -> None:
        """插件执行参数按实际插件 ID 映射，不依赖内置 ID 名单。"""
        execution = PluginExecutionConfig.model_validate(
            {
                "stop_timeout_seconds": 3,
                "plugins": {
                    "custom_plugin": {"consumers_count": 2, "priority": 7}
                },
            }
        )

        self.assertEqual(execution.for_plugin("custom_plugin").consumers_count, 2)
        with self.assertRaisesRegex(KeyError, "missing_plugin"):
            _ = execution.for_plugin("missing_plugin")

    def test_provider_rejects_missing_or_unused_plugin_execution_config(self) -> None:
        """启动时要求动态执行配置与实际加载插件完全一致。"""
        config = MyBotConfig.model_validate(load_example())
        cases = (
            ({key: value for key, value in config.plugin_execution.plugins.items() if key != "ai_group_chat"}, "ai_group_chat"),
            ({**config.plugin_execution.plugins, "missing_plugin": config.plugin_execution.plugins["ai_group_chat"]}, "missing_plugin"),
        )
        for plugins, expected_id in cases:
            with self.subTest(plugin_id=expected_id):
                execution = config.plugin_execution.model_copy(
                    update={"plugins": plugins}
                )
                invalid_config = config.model_copy(
                    update={"plugin_execution": execution}
                )
                manager = cast(
                    ConfigManager,
                    SimpleNamespace(boot_config=invalid_config),
                )

                with self.assertRaisesRegex(ValueError, expected_id):
                    _ = MyProvider(config_manager=manager)


if __name__ == "__main__":
    unittest.main()
