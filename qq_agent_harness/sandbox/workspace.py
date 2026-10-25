"""文件操作与执行共用锁；不跟随工作区中的符号链接。"""

import os
import shutil
import stat
from pathlib import Path, PurePosixPath

from ..contracts import JSON_OBJECT, JsonObject


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(
        self, relative: str, *, allow_root: bool = False, allow_leaf_symlink: bool = False
    ) -> Path:
        if "\\" in relative or ":" in relative or "\x00" in relative:
            raise ValueError("工作区路径必须为相对 POSIX 路径")
        parts = PurePosixPath(relative).parts
        if PurePosixPath(relative).is_absolute() or ".." in parts:
            raise ValueError("路径不能逃逸共享工作区")
        target = self.root
        for index, part in enumerate(parts):
            target = target / part
            if target.is_symlink() and not (allow_leaf_symlink and index == len(parts) - 1):
                raise ValueError("文件操作不能跟随符号链接")
        if target == self.root and not allow_root:
            raise ValueError("此操作需要指定工作区内的文件")
        if not target.is_relative_to(self.root):
            raise ValueError("路径不能逃逸共享工作区")
        return target

    def files(self, relative: str = "") -> JsonObject:
        folder = self.path(relative, allow_root=True)
        if not folder.is_dir():
            raise ValueError("目录不存在")
        entries: list[JsonObject] = []
        for item in sorted(
            folder.iterdir(), key=lambda p: (p.is_symlink() or not p.is_dir(), p.name)
        ):
            if len(entries) >= 1000:
                break
            stat = item.lstat()
            entries.append(
                {
                    "path": item.relative_to(self.root).as_posix(),
                    "name": item.name,
                    "size": stat.st_size,
                    "is_dir": not item.is_symlink() and item.is_dir(),
                    "is_symlink": item.is_symlink(),
                }
            )
        return JSON_OBJECT.validate_python(
            {"path": folder.relative_to(self.root).as_posix(), "files": entries}
        )

    def read(self, relative: str, max_bytes: int = 50_000_000) -> bytes:
        path = self.path(relative)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("只能读取普通文件，不能读取管道、设备或目录")
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError("文件超过本次读取上限")
        return content

    def write(self, relative: str, content: bytes, *, overwrite: bool = False) -> None:
        path = self.path(relative)
        if path.exists() and not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("只能写入普通文件")
        if not path.parent.is_dir():
            raise ValueError("父目录不存在；可先通过代码工具创建")
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        flags |= os.O_TRUNC if overwrite else os.O_EXCL
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            if os.name != "nt":
                os.fchown(stream.fileno(), 1000, 1000)
            stream.write(content)

    def make_parents(self, relative: str) -> None:
        destination = self.path(relative)
        parent = self.root
        for component in destination.parent.relative_to(self.root).parts:
            parent = parent / component
            if not parent.exists():
                parent.mkdir(mode=0o700)
                if os.name != "nt":
                    os.chown(parent, 1000, 1000)

    def cleanup(self, mode: str, paths: list[str]) -> list[str]:
        if mode == "cache":
            targets = [".cache", ".tmp"]
        elif mode == "all":
            targets = [p.name for p in self.root.iterdir()]
        elif mode == "selected":
            targets = paths
        else:
            raise ValueError("未知清理范围")
        # 先完整校验，避免前半批已删除后才发现恶意路径。
        checked = [(name, self.path(name, allow_leaf_symlink=True)) for name in targets]
        removed: list[str] = []
        for name, path in checked:
            if path.is_symlink():
                path.unlink()
                removed.append(name)
                continue
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(name)
        return removed

    def prepare(self) -> None:
        for relative in (".home", ".cache", ".tmp"):
            path = self.path(relative)
            path.mkdir(exist_ok=True)
            if os.name != "nt":
                os.chown(path, 1000, 1000)
