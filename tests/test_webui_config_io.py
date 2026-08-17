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


def commented_config() -> str:
    """生成带注释的最小配置，用于验证写回保真。"""
    return textwrap.dedent(
        """
        # 服务监听配置
        [server]
        port = 6055  # NapCat 反向 WS 端口

        [napcat]
        websocket_token = "test-token"

        [database]
        password = "test-password"

        [llm.providers.main]
        api_key = "test-api-key"
        max_attempts = 3
        retry_delay_seconds = 0

        # 群通知插件
        [plugins.group_notice]
        groups = ["40000"]
        send_avatar = true
        """
    ).strip() + "\n"


class WebUIConfigIOTest(unittest.TestCase):
    """验证配置读取、校验、写回保注释与乐观锁。"""

    def _manager(self, root: Path) -> ConfigManager:
        return ConfigManager.create(config_file=root / "mybot.toml")

    def test_read_valid_config_returns_payload_and_hash(self) -> None:
        """合法配置返回含默认值和明文密钥的可编辑 dict。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(commented_config(), encoding="utf-8")

            result = read_config_payload(config_file=config_file)

        self.assertTrue(result.valid)
        self.assertEqual(result.issues, ())
        self.assertIsNotNone(result.parsed)
        self.assertEqual(result.config["server"]["port"], 6055)
        self.assertEqual(result.config["server"]["host"], "0.0.0.0")
        self.assertEqual(result.config["storage"]["images"]["directory"], "images")
        self.assertEqual(result.config["napcat"]["websocket_token"], "test-token")
        self.assertEqual(
            result.config["llm"]["providers"]["main"]["api_key"],
            "test-api-key",
        )
        self.assertEqual(len(result.sha256), 64)

    def test_read_invalid_toml_still_returns_issues(self) -> None:
        """TOML 语法错误时返回错误而不是抛出，便于 WebUI 修复。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text("[server\nport =", encoding="utf-8")

            result = read_config_payload(config_file=config_file)

        self.assertFalse(result.valid)
        self.assertIsNone(result.parsed)
        self.assertEqual(result.issues[0].error_type, "toml_decode")

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
        self.assertEqual(written["server"], {"port": 7000})
        self.assertEqual(written["database"], {"password": "test-password"})
        self.assertNotIn("network", written)
        self.assertNotIn("storage", written)

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
            model = { provider = "main", name = "chat", supports_images = false }
            extra_requirements_file = "extra.md"

            [plugins.ai_group_chat.vision]
            model = { provider = "main", name = "vision" }
            system_prompt_file = "vs.md"
            user_prompt_file = "vu.md"

            [[plugins.ai_group_chat.groups]]
            id = "40000"
            system_prompt_file = "sys.md"
            max_context_tokens = 64000
            """
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("extra.md", "vs.md", "vu.md", "sys.md"):
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
                "api_key": "sk-second",
                "max_attempts": 3,
                "retry_delay_seconds": 0,
            }
            new_llm["providers"] = new_providers
            new_config["llm"] = new_llm
            new_plugins = dict(payload.config["plugins"])
            new_plugins["image_generate"] = {
                "groups": ["40000"],
                "model": {"provider": "second", "name": "image"},
            }
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
