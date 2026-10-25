"""WebUI 配置读写（config_io）测试。"""

import tempfile
import textwrap
import tomllib
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.config import ConfigLoadError, ConfigManager
from app.webui.config_io import (
    ConfigConflictError,
    parse_and_validate,
    read_config_payload,
    write_config_payload,
)
from tests.config_helpers import build_ai_group_chat_config, minimal_config_toml


def commented_config() -> str:
    """生成带注释的最小配置，用于验证写回保真。"""
    return minimal_config_toml(with_comments=True)


class WebUIConfigIOTest(unittest.TestCase):
    """验证配置读取、校验、写回保注释与乐观锁。"""

    def _manager(self, root: Path) -> ConfigManager:
        return ConfigManager.create(config_file=root / "mybot.toml")

    def test_validation_issues_do_not_echo_secret(self) -> None:
        """校验失败摘要不包含同文件的 API key。"""
        secret = "sk-never-log-this"
        payload: dict[str, Any] = tomllib.loads(
            commented_config().replace("test-api-key", secret).replace(
                "max_attempts = 3", "max_attempts = 0"
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(commented_config(), encoding="utf-8")

            with self.assertRaises(ConfigLoadError) as caught:
                parse_and_validate(config_file=config_file, payload=payload)

        self.assertNotIn(secret, str(caught.exception))

    def test_write_preserves_comments_and_inline_format(self) -> None:
        """修改端口后原有注释与文件结构保持。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(commented_config(), encoding="utf-8")
            manager = self._manager(Path(temp_dir))
            payload = read_config_payload(config_file=config_file)

            new_config = dict(payload.config)
            new_config["server"] = {**payload.config["server"], "port": 7000}
            new_config["database"] = {
                **payload.config["database"],
                "password_file": "",
            }
            new_config["network"] = {**payload.config["network"], "proxy": ""}
            result = write_config_payload(
                config_file=config_file,
                payload=new_config,
                base_sha256=payload.sha256,
                boot_config=manager.boot_config,
            )

            new_text = config_file.read_text(encoding="utf-8")

        self.assertIn("# 服务监听配置", new_text)
        self.assertIn("# 群通知插件", new_text)
        self.assertIn("port = 7000", new_text)
        self.assertIn("retry_delay_seconds = 0\n", new_text)
        self.assertNotIn("retry_delay_seconds = 0.0", new_text)
        self.assertEqual(result.restart_required_sections, ("server",))
        self.assertEqual(result.config["server"]["port"], 7000)
        written = tomllib.loads(new_text)
        self.assertEqual(written["server"]["port"], 7000)
        self.assertEqual(written["server"]["config_watch_debounce_ms"], 500)
        self.assertEqual(written["database"]["password"], "test-password")
        self.assertNotIn("password_file", written["database"])
        self.assertEqual(written["network"]["timeout_seconds"], 15)
        self.assertNotIn("proxy", written["network"])
        self.assertEqual(written["storage"]["images"]["download_concurrency"], 16)

    def test_switch_mcp_transport_preserves_other_services_and_header_secret(self) -> None:
        """切换 HTTP 时删除旧进程字段，保留其余配置、注释及可编辑认证头。"""
        original = commented_config().replace('servers = {}', '') + textwrap.dedent('''
            # 远程工具配置
            [mcp.servers.remote]
            command = "old-command"
            args = []
            disabled = false
            env = { OLD_API_URL = "http://old-service:3002" }

            [mcp.servers.other]
            command = "other-command"
            args = ["stdio"]
            disabled = false
            env = { OTHER_API_KEY = "unchanged" }
        ''')
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(original, encoding="utf-8")
            manager = self._manager(Path(temp_dir))
            current = read_config_payload(config_file=config_file)
            payload = current.config
            payload["mcp"]["servers"]["remote"] = {
                "transport": "streamable_http", "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer test-secret"}, "disabled": False,
            }
            _ = write_config_payload(
                config_file=config_file, payload=payload,
                base_sha256=current.sha256, boot_config=manager.boot_config,
            )
            text = config_file.read_text(encoding="utf-8")
            written = tomllib.loads(text)
            reloaded = read_config_payload(config_file=config_file)

        self.assertEqual(set(written["mcp"]["servers"]["remote"]), {"transport", "url", "headers", "disabled"})
        self.assertEqual(written["mcp"]["servers"]["other"], tomllib.loads(original)["mcp"]["servers"]["other"])
        self.assertIn("# 远程工具配置", text)
        self.assertEqual(reloaded.config["mcp"]["servers"]["remote"]["headers"], {"Authorization": "Bearer test-secret"})

    def test_blank_password_does_not_conflict_with_password_file(self) -> None:
        """WebUI 空密码与已配置的 secret 文件可以一起提交。"""
        original = commented_config().replace(
            'password = "test-password"',
            'password_file = "/run/secrets/postgres_password"',
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "mybot.toml"
            config_file.write_text(original, encoding="utf-8")
            manager = self._manager(root)
            current = read_config_payload(config_file=config_file)
            payload = dict(current.config)
            payload["database"] = {
                **current.config["database"],
                "password": "",
            }

            _ = write_config_payload(
                config_file=config_file,
                payload=payload,
                base_sha256=current.sha256,
                boot_config=manager.boot_config,
            )
            written = tomllib.loads(config_file.read_text(encoding="utf-8"))

        self.assertNotIn("password", written["database"])
        self.assertEqual(
            written["database"]["password_file"],
            "/run/secrets/postgres_password",
        )

    def test_write_removes_and_adds_plugin_sections(self) -> None:
        """payload 缺失的插件节被删除，新节被追加。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(commented_config(), encoding="utf-8")
            manager = self._manager(Path(temp_dir))
            payload = read_config_payload(config_file=config_file)

            new_config = dict(payload.config)
            new_plugins = dict(payload.config["plugins"])
            del new_plugins["group_notice"]
            new_plugins["auto_unban"] = {"protected_users": ["10000"]}
            new_config["plugins"] = new_plugins
            _ = write_config_payload(
                config_file=config_file,
                payload=new_config,
                base_sha256=payload.sha256,
                boot_config=manager.boot_config,
            )

            written = tomllib.loads(config_file.read_text(encoding="utf-8"))

        plugins = written["plugins"]
        self.assertNotIn("group_notice", plugins)
        self.assertEqual(plugins["auto_unban"]["protected_users"], ["10000"])

    def test_write_replaces_array_of_tables(self) -> None:
        """AI 群聊 groups 数组整体替换后结构正确。"""
        raw = commented_config() + textwrap.dedent(
            """

            [plugins.ai_group_chat]
            model = { provider = "main", name = "chat" }
            max_tool_rounds = 16
            token_safety_factor = 1.05
            context_compression_notice = "正在整理上下文"
            forward_reply_threshold_chars = 1000
            extra_requirements_file = "extra.md"
            allow_mention_all = false
            tool_result_retention = "off"

            [plugins.ai_group_chat.images]
            max_per_turn = 0
            fetch_concurrency = 16
            download_timeout_seconds = 20
            max_image_bytes = 0
            max_total_bytes_per_request = 0
            max_width = 0
            max_height = 0
            allowed_mime_types = []
            oversize_behavior = "skip"
            image_detail = "auto"
            retain_images = false
            forward_tool_enabled = true
            forward_max_per_call = 0
            forward_max_per_turn = 0

            [plugins.ai_group_chat.videos]
            enabled = false
            max_per_turn = 2
            fetch_concurrency = 2
            download_timeout_seconds = 60
            max_video_bytes = 20971520

            [plugins.ai_group_chat.formatting]
            field_text_limit = 0
            json_text_limit = 0
            markdown_text_limit = 0
            forward_max_items = 0
            forward_max_depth = -1
            nested_text_search_max_depth = -1

            [plugins.ai_group_chat.history]
            default_limit = 20
            max_per_call = 0
            default_before_count = 10
            default_after_count = 10

            [plugins.ai_group_chat.files]
            default_count = 50
            max_per_call = 0

            [plugins.ai_group_chat.token_estimator]
            request_overhead_tokens = 128
            message_overhead_tokens = 16
            tool_call_overhead_tokens = 64
            image_tokens = 1024
            ascii_tokens_per_character = 1
            non_ascii_tokens_per_character = 2

            [[plugins.ai_group_chat.groups]]
            id = "40000"
            system_prompt_file = "sys.md"
            max_context_tokens = 64000
            """
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("extra.md", "sys.md"):
                (root / name).write_text("内容", encoding="utf-8")
            config_file = root / "mybot.toml"
            config_file.write_text(raw, encoding="utf-8")
            manager = self._manager(root)
            payload = read_config_payload(config_file=config_file)

            new_config = dict(payload.config)
            new_plugins = dict(payload.config["plugins"])
            new_ai = dict(payload.config["plugins"]["ai_group_chat"])
            new_ai["groups"] = [
                {
                    "id": "50000",
                    "system_prompt_file": "sys.md",
                    "max_context_tokens": 32000,
                },
                {
                    "id": "60000",
                    "system_prompt_file": "sys.md",
                    "max_context_tokens": 16000,
                },
            ]
            new_plugins["ai_group_chat"] = new_ai
            new_config["plugins"] = new_plugins
            _ = write_config_payload(
                config_file=config_file,
                payload=new_config,
                base_sha256=payload.sha256,
                boot_config=manager.boot_config,
            )

            written = tomllib.loads(config_file.read_text(encoding="utf-8"))

        groups = written["plugins"]["ai_group_chat"]["groups"]
        self.assertEqual([group["id"] for group in groups], ["50000", "60000"])
        self.assertEqual(groups[0]["max_context_tokens"], 32000)

    def test_dynamic_record_keys_with_dots_roundtrip_unchanged(self) -> None:
        """Provider ID 和 MCP 服务名中的点号不会被拆成嵌套对象。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "mybot.toml"
            config_file.write_text(commented_config(), encoding="utf-8")
            manager = self._manager(root)
            current = read_config_payload(config_file=config_file)
            payload = dict(current.config)
            payload["llm"] = {
                "providers": {
                    **current.config["llm"]["providers"],
                    "model.service": {
                        "api_format": "chat_completions",
                        "api_key": "dot-key",
                        "base_url": "https://example.com/v1",
                        "proxy": None,
                        "inherit_network_proxy": True,
                        "timeout_seconds": 0,
                        "max_attempts": 5,
                        "retry_delay_seconds": 0,
                        "retry_max_delay_seconds": 10,
                    },
                }
            }
            payload["mcp"] = {
                "enabled": True,
                "initialization_timeout_seconds": 30,
                "call_timeout_seconds": 60,
                "servers": {
                    "vendor.tool": {
                        "command": "npx",
                        "args": ["-y"],
                        "env": {"API.KEY": "value"},
                        "cwd": None,
                        "disabled": False,
                    }
                },
            }

            _ = write_config_payload(
                config_file=config_file,
                payload=payload,
                base_sha256=current.sha256,
                boot_config=manager.boot_config,
            )
            written = tomllib.loads(config_file.read_text(encoding="utf-8"))

        self.assertIn("model.service", written["llm"]["providers"])
        self.assertNotIn("model", written["llm"]["providers"])
        self.assertIn("vendor.tool", written["mcp"]["servers"])
        self.assertEqual(
            written["mcp"]["servers"]["vendor.tool"]["env"]["API.KEY"],
            "value",
        )

    def test_write_rejects_stale_hash_and_keeps_file(self) -> None:
        """乐观锁失配时拒绝写入且文件内容不变。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            original = commented_config()
            config_file.write_text(original, encoding="utf-8")
            manager = self._manager(Path(temp_dir))
            payload = read_config_payload(config_file=config_file)

            with self.assertRaises(ConfigConflictError):
                write_config_payload(
                    config_file=config_file,
                    payload=payload.config,
                    base_sha256="0" * 64,
                    boot_config=manager.boot_config,
                )

            self.assertEqual(config_file.read_text(encoding="utf-8"), original)

    def test_concurrent_writes_allow_only_one_matching_hash(self) -> None:
        """同一旧哈希的并发写入只有一个能成功，避免静默互相覆盖。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(commented_config(), encoding="utf-8")
            manager = self._manager(Path(temp_dir))
            current = read_config_payload(config_file=config_file)

            def write_port(port: int) -> bool:
                payload = dict(current.config)
                payload["server"] = {**current.config["server"], "port": port}
                try:
                    _ = write_config_payload(
                        config_file=config_file,
                        payload=payload,
                        base_sha256=current.sha256,
                        boot_config=manager.boot_config,
                    )
                except ConfigConflictError:
                    return False
                return True

            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(write_port, range(7000, 7004)))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 3)

    def test_write_rejects_invalid_payload_and_keeps_file(self) -> None:
        """非法 payload 不落盘。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            original = commented_config()
            config_file.write_text(original, encoding="utf-8")
            manager = self._manager(Path(temp_dir))
            payload = read_config_payload(config_file=config_file)

            new_config = dict(payload.config)
            new_config["server"] = {**payload.config["server"], "port": 70000}
            with self.assertRaises(ConfigLoadError):
                write_config_payload(
                    config_file=config_file,
                    payload=new_config,
                    base_sha256=payload.sha256,
                    boot_config=manager.boot_config,
                )

            self.assertEqual(config_file.read_text(encoding="utf-8"), original)

    def test_plugin_can_reference_provider_added_in_same_payload(self) -> None:
        """同次提交新增 provider 并引用时校验通过，重启提示包含 llm。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(commented_config(), encoding="utf-8")
            manager = self._manager(Path(temp_dir))
            payload = read_config_payload(config_file=config_file)

            new_config = dict(payload.config)
            new_llm = dict(payload.config["llm"])
            new_providers = dict(payload.config["llm"]["providers"])
            new_providers["second"] = {
                "api_format": "chat_completions",
                "api_key": "sk-second",
                "inherit_network_proxy": True,
                "timeout_seconds": 0,
                "max_attempts": 3,
                "retry_delay_seconds": 0,
                "retry_max_delay_seconds": 10,
            }
            new_llm["providers"] = new_providers
            new_config["llm"] = new_llm
            new_plugins = dict(payload.config["plugins"])
            (Path(temp_dir) / "extra.md").write_text("通用要求", encoding="utf-8")
            new_plugins["ai_group_chat"] = build_ai_group_chat_config(
                provider="second"
            ).model_dump(mode="json", exclude_none=True)
            new_config["plugins"] = new_plugins
            result = write_config_payload(
                config_file=config_file,
                payload=new_config,
                base_sha256=payload.sha256,
                boot_config=manager.boot_config,
            )

        self.assertIn("llm", result.restart_required_sections)


if __name__ == "__main__":
    unittest.main()
