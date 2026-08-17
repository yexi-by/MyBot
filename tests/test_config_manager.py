"""统一配置加载和自动热加载测试。"""

import asyncio
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.config import (
    AutoUnbanConfig,
    ConfigLoadError,
    ConfigManager,
    ConfigWatcher,
    GroupNoticeConfig,
    load_config,
)


def base_config(*, group_id: str = "40000", port: int = 6055) -> str:
    """生成不依赖外部文件的最小统一配置。"""
    return textwrap.dedent(
        f"""
        [server]
        port = {port}

        [napcat]
        websocket_token = "test-token"

        [database]
        password = "test-password"

        [llm.providers.main]
        api_key = "test-api-key"
        max_attempts = 3
        retry_delay_seconds = 0

        [plugins.group_notice]
        groups = ["{group_id}"]
        send_avatar = true
        """
    ).strip() + "\n"


def write_ai_files(config_root: Path) -> None:
    """写入 AI 配置实际引用的文本文件。"""
    files = {
        "ai/extra.md": "通用要求",
        "ai/system.md": "角色提示",
        "ai/knowledge.md": "知识内容",
        "ai/vision-system.md": "只描述事实",
        "ai/vision-user.md": "结合问题描述",
    }
    for relative, content in files.items():
        path = config_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def ai_config() -> str:
    """生成包含全部 AI 文件引用的统一配置。"""
    return base_config() + textwrap.dedent(
        """

        [plugins.ai_group_chat]
        model = { provider = "main", name = "chat", supports_images = false }
        extra_requirements_file = "ai/extra.md"

        [plugins.ai_group_chat.vision]
        model = { provider = "main", name = "vision" }
        system_prompt_file = "ai/vision-system.md"
        user_prompt_file = "ai/vision-user.md"

        [[plugins.ai_group_chat.groups]]
        id = "40000"
        system_prompt_file = "ai/system.md"
        knowledge_base_file = "ai/knowledge.md"
        max_context_tokens = 64000
        """
    )


class ConfigManagerTest(unittest.IsolatedAsyncioTestCase):
    """验证配置边界、部分应用和 watcher 生命周期。"""

    def test_materializes_only_referenced_files(self) -> None:
        """AI 运行快照直接包含组合后的 system prompt。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "mybot.toml"
            write_ai_files(root)
            config_file.write_text(ai_config(), encoding="utf-8")

            loaded = load_config(config_file=config_file)

        ai = loaded.plugins.ai_group_chat
        self.assertIsNotNone(ai)
        assert ai is not None
        self.assertEqual(len(ai.groups), 1)
        self.assertEqual(
            ai.groups[0].system_prompt,
            "角色提示\n\n知识内容\n\n通用要求",
        )
        self.assertEqual(len(loaded.plugins.referenced_files), 6)

    def test_rejects_file_outside_config_directory(self) -> None:
        """prompt 不能通过父目录逃出统一配置目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "config"
            root.mkdir()
            outside = root.parent / "outside.md"
            outside.write_text("外部内容", encoding="utf-8")
            write_ai_files(root)
            config_file = root / "mybot.toml"
            config_file.write_text(
                ai_config().replace('"ai/system.md"', '"../outside.md"'),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigLoadError) as caught:
                _ = load_config(config_file=config_file)

        self.assertIn("不能位于 config 目录外", str(caught.exception))

    def test_rejects_absolute_referenced_file(self) -> None:
        """插件 prompt 不能使用绝对路径。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_ai_files(root)
            absolute_path = (root / "ai/system.md").resolve().as_posix()
            config_file = root / "mybot.toml"
            config_file.write_text(
                ai_config().replace('"ai/system.md"', f'"{absolute_path}"'),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigLoadError) as caught:
                _ = load_config(config_file=config_file)

        self.assertIn("必须使用相对 config 目录的路径", str(caught.exception))

    def test_rejects_symlink_to_file_outside_config_directory(self) -> None:
        """配置目录内的符号链接也不能指向目录外。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "config"
            root.mkdir()
            write_ai_files(root)
            outside = root.parent / "outside.md"
            outside.write_text("外部内容", encoding="utf-8")
            link = root / "ai/outside-link.md"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"当前系统不允许创建符号链接: {type(exc).__name__}")
            config_file = root / "mybot.toml"
            config_file.write_text(
                ai_config().replace('"ai/system.md"', '"ai/outside-link.md"'),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigLoadError) as caught:
                _ = load_config(config_file=config_file)

        self.assertIn("不能位于 config 目录外", str(caught.exception))

    def test_required_prompt_is_nonempty_but_knowledge_base_may_be_empty(self) -> None:
        """system 等指令文件拒绝空内容，知识库空文件按未提供处理。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_ai_files(root)
            config_file = root / "mybot.toml"
            config_file.write_text(ai_config(), encoding="utf-8")
            (root / "ai/system.md").write_text("  \n", encoding="utf-8")

            with self.assertRaises(ConfigLoadError) as caught:
                _ = load_config(config_file=config_file)
            self.assertIn("文件内容不能为空", str(caught.exception))

            (root / "ai/system.md").write_text("角色提示", encoding="utf-8")
            (root / "ai/knowledge.md").write_text("", encoding="utf-8")
            loaded = load_config(config_file=config_file)

        ai = loaded.plugins.ai_group_chat
        self.assertIsNotNone(ai)
        assert ai is not None
        self.assertEqual(ai.groups[0].system_prompt, "角色提示\n\n通用要求")

    def test_valid_boot_change_does_not_block_plugin_reload(self) -> None:
        """端口变化只提示重启，群列表仍立即更新。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            config_file.write_text(
                base_config(group_id="50000", port=7000),
                encoding="utf-8",
            )

            result = manager.reload()

        self.assertTrue(result.applied)
        self.assertEqual(result.changed_plugins, ("group_notice",))
        self.assertEqual(result.restart_required_sections, ("server",))
        notice = manager.plugins.group_notice
        self.assertIsNotNone(notice)
        assert notice is not None
        self.assertEqual(notice.groups, ("50000",))
        self.assertEqual(manager.boot_config.server.port, 6055)

    def test_invalid_file_keeps_previous_snapshot(self) -> None:
        """无效文件不会增加版本或改变插件配置。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            config_file.write_text("[plugins.group_notice", encoding="utf-8")

            result = manager.reload()
            self.assertFalse(result.applied)
            self.assertIsNotNone(result.error)
            self.assertEqual(manager.plugins.revision, 1)
            notice = manager.plugins.group_notice
            self.assertIsNotNone(notice)
            assert notice is not None
            self.assertEqual(notice.groups, ("40000",))

            config_file.write_text(base_config(group_id="50000"), encoding="utf-8")
            repaired = manager.reload()
            self.assertTrue(repaired.applied)
            repaired_notice = manager.plugins.group_notice
            self.assertIsNotNone(repaired_notice)
            assert repaired_notice is not None
            self.assertEqual(repaired_notice.groups, ("50000",))

    def test_missing_config_file_keeps_previous_snapshot_and_can_recover(self) -> None:
        """原子替换期间文件短暂缺失不会破坏现有配置。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            config_file.unlink()

            missing = manager.reload()
            self.assertFalse(missing.applied)
            self.assertIsNotNone(missing.error)
            self.assertEqual(manager.plugins.revision, 1)

            config_file.write_text(base_config(group_id="50000"), encoding="utf-8")
            recovered = manager.reload()

        self.assertTrue(recovered.applied)
        notice = manager.plugins.group_notice
        self.assertIsNotNone(notice)
        assert notice is not None
        self.assertEqual(notice.groups, ("50000",))

    def test_plugin_section_can_be_removed_and_added_again(self) -> None:
        """删除配置节立即停用插件，重新加入后重新启用。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            disabled = base_config().replace(
                '\n[plugins.group_notice]\ngroups = ["40000"]\nsend_avatar = true\n',
                "\n",
            )
            config_file.write_text(disabled, encoding="utf-8")

            removed = manager.reload()
            self.assertTrue(removed.applied)
            self.assertIsNone(manager.plugins.group_notice)

            config_file.write_text(base_config(group_id="50000"), encoding="utf-8")
            restored = manager.reload()

        self.assertTrue(restored.applied)
        self.assertEqual(manager.plugins.revision, 3)
        notice = manager.plugins.group_notice
        self.assertIsNotNone(notice)
        assert notice is not None
        self.assertEqual(notice.groups, ("50000",))

    def test_plugin_view_cannot_read_another_config_type(self) -> None:
        """插件视图只返回绑定 plugin_id 对应的类型化配置。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            view = manager.bind_plugin("group_notice")

        notice = view.get(GroupNoticeConfig)
        self.assertIsNotNone(notice)
        with self.assertRaises(AttributeError):
            setattr(view, "plugin_id", "auto_unban")
        with self.assertRaises(TypeError):
            _ = view.get(AutoUnbanConfig)
        with self.assertRaises(KeyError):
            _ = manager.bind_plugin("external_plugin")

    def test_new_provider_requires_restart_before_plugin_can_reference_it(self) -> None:
        """同次新增 provider 不能被热加载插件提前使用。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            candidate = base_config() + textwrap.dedent(
                """

                [llm.providers.new]
                api_key = "new-secret"
                max_attempts = 3
                retry_delay_seconds = 0

                [plugins.image_generate]
                groups = ["40000"]
                model = { provider = "new", name = "image" }
                """
            )
            config_file.write_text(candidate, encoding="utf-8")

            result = manager.reload()

        self.assertFalse(result.applied)
        self.assertIsNotNone(result.error)
        self.assertIsNone(manager.plugins.image_generate)

    def test_validation_error_does_not_echo_secret_input(self) -> None:
        """配置错误摘要不会包含同文件中的 API key。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            secret = "sk-never-log-this"
            invalid = base_config().replace("test-api-key", secret).replace(
                "max_attempts = 3",
                "max_attempts = 0",
            )
            config_file.write_text(invalid, encoding="utf-8")

            with self.assertRaises(ConfigLoadError) as caught:
                _ = load_config(config_file=config_file)

        self.assertNotIn(secret, str(caught.exception))

    async def test_watcher_applies_saved_plugin_config_and_stops(self) -> None:
        """保存配置文件后自动增加版本，停止时不遗留任务。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            watcher = ConfigWatcher(manager=manager)
            task = asyncio.create_task(watcher.run())
            await asyncio.sleep(0.2)
            config_file.write_text(base_config(group_id="50000"), encoding="utf-8")
            try:
                async with asyncio.timeout(5):
                    while manager.plugins.revision == 1:
                        await asyncio.sleep(0.05)
            finally:
                watcher.stop()
                await asyncio.wait_for(task, timeout=3)

        notice = manager.plugins.group_notice
        self.assertIsNotNone(notice)
        assert notice is not None
        self.assertEqual(notice.groups, ("50000",))

    async def test_watcher_coalesces_consecutive_writes(self) -> None:
        """短时间连续保存只发布最后一个有效插件版本。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            watcher = ConfigWatcher(manager=manager)
            task = asyncio.create_task(watcher.run())
            await asyncio.sleep(0.2)
            for group_id in ("50000", "60000", "70000"):
                config_file.write_text(base_config(group_id=group_id), encoding="utf-8")
            try:
                async with asyncio.timeout(5):
                    while manager.plugins.revision == 1:
                        await asyncio.sleep(0.05)
                await asyncio.sleep(0.7)
            finally:
                watcher.stop()
                await asyncio.wait_for(task, timeout=3)

        self.assertEqual(manager.plugins.revision, 2)
        notice = manager.plugins.group_notice
        self.assertIsNotNone(notice)
        assert notice is not None
        self.assertEqual(notice.groups, ("70000",))

    async def test_watcher_recovers_when_missing_referenced_file_is_created(
        self,
    ) -> None:
        """新 prompt 暂时缺失时保留旧配置，创建文件后自动再次加载。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_ai_files(root)
            config_file = root / "mybot.toml"
            config_file.write_text(ai_config(), encoding="utf-8")
            manager = ConfigManager.create(config_file=config_file)
            watcher = ConfigWatcher(manager=manager)
            task = asyncio.create_task(watcher.run())
            await asyncio.sleep(0.2)
            missing_prompt = root / "ai/new-system.md"
            config_file.write_text(
                ai_config().replace('"ai/system.md"', '"ai/new-system.md"'),
                encoding="utf-8",
            )
            try:
                async with asyncio.timeout(5):
                    while missing_prompt.resolve() not in manager.watched_files:
                        await asyncio.sleep(0.05)
                self.assertEqual(manager.plugins.revision, 1)
                missing_prompt.write_text("新角色提示", encoding="utf-8")
                async with asyncio.timeout(5):
                    while manager.plugins.revision == 1:
                        await asyncio.sleep(0.05)
            finally:
                watcher.stop()
                await asyncio.wait_for(task, timeout=3)

        ai = manager.plugins.ai_group_chat
        self.assertIsNotNone(ai)
        assert ai is not None
        self.assertEqual(
            ai.groups[0].system_prompt,
            "新角色提示\n\n知识内容\n\n通用要求",
        )


if __name__ == "__main__":
    unittest.main()
