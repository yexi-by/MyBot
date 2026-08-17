"""WebUI 文本文件读写（files）测试。"""

import tempfile
import unittest
from pathlib import Path

from app.config import ConfigLoadError
from app.webui.config_io import ConfigConflictError
from app.webui.files import list_text_files, read_text_file, write_text_file


class WebUIFilesTest(unittest.TestCase):
    """验证 config/ 内文本文件的列表、读写、新建与逃逸防护。"""

    def _root(self, temp_dir: str) -> Path:
        root = Path(temp_dir)
        (root / "ai/prompts").mkdir(parents=True)
        (root / "ai/prompts/system.md").write_text("角色提示", encoding="utf-8")
        (root / "ai/knowledge.md").write_text("知识", encoding="utf-8")
        (root / "notes.txt").write_text("备注", encoding="utf-8")
        (root / "mybot.toml").write_text("[server]\n", encoding="utf-8")
        (root / "image.png").write_bytes(b"\x89PNG")
        return root

    def test_list_only_returns_text_files(self) -> None:
        """列表只包含 config/ 内的 md/txt，排除 toml 与二进制。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._root(temp_dir)

            files = list_text_files(config_root=root)

        self.assertEqual(
            files,
            ["ai/knowledge.md", "ai/prompts/system.md", "notes.txt"],
        )

    def test_read_returns_content_and_hash(self) -> None:
        """读取返回 UTF-8 内容与稳定哈希。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._root(temp_dir)

            content, digest = read_text_file(
                config_root=root, relative_path="ai/prompts/system.md"
            )

        self.assertEqual(content, "角色提示")
        self.assertEqual(len(digest), 64)

    def test_read_rejects_escape_and_absolute_path(self) -> None:
        """父目录逃逸与绝对路径被拒绝。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._root(temp_dir)
            outside = root.parent / "outside.md"
            outside.write_text("外部", encoding="utf-8")

            with self.assertRaises(ConfigLoadError):
                read_text_file(config_root=root, relative_path="../outside.md")
            absolute = outside.resolve().as_posix()
            with self.assertRaises(ConfigLoadError):
                read_text_file(config_root=root, relative_path=absolute)

    def test_read_and_write_reject_non_text_paths(self) -> None:
        """文本 API 不能绕过配置校验读写 TOML 或其他文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._root(temp_dir)

            with self.assertRaises(ConfigLoadError):
                read_text_file(config_root=root, relative_path="mybot.toml")
            with self.assertRaises(ConfigLoadError):
                write_text_file(
                    config_root=root,
                    relative_path="mybot.toml",
                    content="[server]\nport = 1\n",
                    base_sha256=None,
                )
            with self.assertRaises(ConfigLoadError):
                write_text_file(
                    config_root=root,
                    relative_path=r"ai\prompt.md",
                    content="内容",
                    base_sha256=None,
                )

    def test_read_rejects_symlink_escape(self) -> None:
        """指向 config/ 外的符号链接被拒绝。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._root(temp_dir)
            outside = root.parent / "outside.md"
            outside.write_text("外部", encoding="utf-8")
            link = root / "link.md"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"当前系统不允许创建符号链接: {type(exc).__name__}")

            with self.assertRaises(ConfigLoadError):
                read_text_file(config_root=root, relative_path="link.md")
            self.assertNotIn("link.md", list_text_files(config_root=root))

    def test_write_existing_file_enforces_optimistic_lock(self) -> None:
        """已存在文件必须带正确哈希才能写回。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._root(temp_dir)
            _, digest = read_text_file(config_root=root, relative_path="notes.txt")

            with self.assertRaises(ConfigConflictError):
                write_text_file(
                    config_root=root,
                    relative_path="notes.txt",
                    content="新备注",
                    base_sha256=None,
                )
            with self.assertRaises(ConfigConflictError):
                write_text_file(
                    config_root=root,
                    relative_path="notes.txt",
                    content="新备注",
                    base_sha256="0" * 64,
                )
            new_digest = write_text_file(
                config_root=root,
                relative_path="notes.txt",
                content="新备注",
                base_sha256=digest,
            )

            content, _ = read_text_file(config_root=root, relative_path="notes.txt")

        self.assertEqual(content, "新备注")
        self.assertNotEqual(new_digest, digest)

    def test_write_creates_new_file_in_existing_directory(self) -> None:
        """允许在已有目录新建文件，拒绝不存在的父目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._root(temp_dir)

            _ = write_text_file(
                config_root=root,
                relative_path="ai/new-role.md",
                content="新角色",
                base_sha256=None,
            )
            with self.assertRaises(ConfigLoadError):
                write_text_file(
                    config_root=root,
                    relative_path="missing/dir/file.md",
                    content="内容",
                    base_sha256=None,
                )

            files = list_text_files(config_root=root)

        self.assertIn("ai/new-role.md", files)


if __name__ == "__main__":
    unittest.main()
