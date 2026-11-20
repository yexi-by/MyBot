"""工作区文件边界与空间计量；严格限制在工作区根目录内，不跟随符号链接逃逸。"""

import errno
import fnmatch
import os
import re
import shutil
import stat
import time
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

from ..contracts import JSON_OBJECT, JsonObject, JsonValue

CleanupMode = Literal["temp", "cache", "selected", "all"]

SYSTEM_DIRS = frozenset({".cache", ".tmp", ".outputs", ".scratch", ".home", ".venv", ".pydeps"})
RESERVED_ROOTS = frozenset({".home", ".cache", ".tmp", ".outputs", ".scratch"})
SKIP_SEARCH_DIRS = frozenset(
    {
        ".cache",
        ".tmp",
        ".outputs",
        ".home",
        ".venv",
        ".pydeps",
        ".git",
        "node_modules",
        "__pycache__",
    }
)
MAX_OUTPUT_FILES = 32
MAX_SEARCH_FILE_BYTES = 10_000_000
USAGE_CACHE_TTL = 5.0


class WorkspaceUnavailable(RuntimeError):
    """工作区目录不可用或达到配置的空间上限。"""


def _remove_tree_force(target: Path) -> None:
    if target.is_symlink() or not target.is_dir():
        target.unlink(missing_ok=True)
        return
    for root_dir, dirs, files in os.walk(target, topdown=False, followlinks=False):
        rp = Path(root_dir)
        try:
            os.chmod(rp, stat.S_IRWXU)
        except OSError:
            pass
        for fn in files:
            fp = rp / fn
            try:
                if not fp.is_symlink():
                    os.chmod(fp, stat.S_IWUSR | stat.S_IRUSR)
                fp.unlink()
            except FileNotFoundError:
                pass
        for dn in dirs:
            dp = rp / dn
            if dp.is_symlink():
                dp.unlink(missing_ok=True)
            else:
                try:
                    os.chmod(dp, stat.S_IRWXU)
                except OSError:
                    pass
                dp.rmdir()
    target.rmdir()


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._cached_bytes: int | None = None
        self._cached_at: float = 0.0

    def invalidate_usage(self) -> None:
        self._cached_bytes = None

    def ensure_distinct_from(self, config_root: Path | None) -> None:
        if config_root is None:
            return
        cfg = config_root.resolve()
        if self.root == cfg or self.root.is_relative_to(cfg) or cfg.is_relative_to(self.root):
            raise WorkspaceUnavailable("工作区目录不能与配置目录重叠或互相包含")

    def used_bytes(self, *, force: bool = False) -> int:
        now = time.monotonic()
        if (
            not force
            and self._cached_bytes is not None
            and (now - self._cached_at) < USAGE_CACHE_TTL
        ):
            return self._cached_bytes
        if not self.root.exists():
            self._cached_bytes = 0
            self._cached_at = now
            return 0
        total = 0
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=True, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not (current / d).is_symlink()]
            for fn in filenames:
                fp = current / fn
                try:
                    st = fp.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
        self._cached_bytes = total
        self._cached_at = now
        return total

    def check_capacity(self, max_bytes: int, incoming_bytes: int = 0) -> int:
        used = self.used_bytes()
        if used + incoming_bytes > max_bytes:
            raise WorkspaceUnavailable("工作区已达到空间预算上限，请先清理临时文件或无用产物")
        return used

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

    def files(self, relative: str = "", offset: int = 0, limit: int = 100) -> JsonObject:
        folder = self.path(relative, allow_root=True)
        if not folder.exists():
            if folder == self.root:
                return {"path": "", "files": [], "total": 0, "next_offset": None}
            raise ValueError("目录不存在")
        if not folder.is_dir():
            raise ValueError("目录不存在")
        is_root = folder == self.root
        entries: list[JsonObject] = []
        items = sorted(folder.iterdir(), key=lambda p: (p.is_symlink() or not p.is_dir(), p.name))
        for item in items[offset : offset + limit]:
            st = item.lstat()
            entries.append(
                {
                    "path": item.relative_to(self.root).as_posix(),
                    "name": item.name,
                    "size": st.st_size,
                    "mtime": round(st.st_mtime, 3),
                    "is_dir": not item.is_symlink() and item.is_dir(),
                    "is_symlink": item.is_symlink(),
                    "is_system": is_root
                    and (item.name.startswith(".") or item.name in SYSTEM_DIRS),
                }
            )
        return JSON_OBJECT.validate_python(
            {
                "path": "" if is_root else folder.relative_to(self.root).as_posix(),
                "files": entries,
                "total": len(items),
                "next_offset": offset + len(entries)
                if offset + len(entries) < len(items)
                else None,
            }
        )

    def read_text(self, relative: str, offset: int = 0, limit: int = 65_536) -> JsonObject:
        path = self.path(relative)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("只能读取普通文件")
        total = path.stat().st_size
        if offset >= total:
            return {
                "path": relative,
                "content": "",
                "offset": offset,
                "total_bytes": total,
                "next_offset": None,
            }
        with path.open("rb") as stream:
            stream.seek(offset)
            content = stream.read(max(4, limit))
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            if error.reason != "unexpected end of data" or offset + len(content) >= total:
                raise ValueError(
                    "文件不是 UTF-8 文本，或 offset 不是字符边界；可用脚本处理二进制文件"
                ) from None
            content = content[: error.start]
            text = content.decode("utf-8")
        end = offset + len(content)
        return {
            "path": relative,
            "content": text,
            "offset": offset,
            "total_bytes": total,
            "next_offset": end if end < total else None,
        }

    def read(self, relative: str, max_bytes: int = 50_000_000) -> bytes:
        path = self.path(relative)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("只能读取普通文件，不能读取管道、设备或目录")
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError("文件超过本次读取上限")
        return content

    def write(
        self, relative: str, content: bytes, *, overwrite: bool = False, append: bool = False
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(relative)
        if path.exists() and not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("只能写入普通文件")
        self.make_parents(relative)
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        flags |= os.O_APPEND if append else os.O_TRUNC if overwrite else os.O_EXCL
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        self.invalidate_usage()

    def open_exclusive_binary(self, relative: str) -> BinaryIO:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(relative)
        self.make_parents(relative)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        fd = os.open(path, flags, 0o600)
        try:
            return os.fdopen(fd, "wb")
        except Exception:
            os.close(fd)
            raise

    def edit_text(
        self,
        relative: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
        max_bytes: int = 50_000_000,
    ) -> JsonObject:
        if not old_string:
            raise ValueError("old_string 不能为空")
        if old_string == new_string:
            raise ValueError("new_string 与 old_string 相同，未发生修改")
        raw = self.read(relative, max_bytes=max_bytes)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("目标文件不是有效的 UTF-8 文本，无法直接替换") from None
        occurrences = text.count(old_string)
        if occurrences == 0:
            raise ValueError("文件中未找到指定的 old_string，请核对原文与缩进")
        if occurrences > 1 and not replace_all:
            raise ValueError(
                f"old_string 在文件中出现了 {occurrences} 次；请提供更多上下文使其唯一，或设置 replace_all=true"
            )
        updated = (
            text.replace(old_string, new_string)
            if replace_all
            else text.replace(old_string, new_string, 1)
        )
        encoded = updated.encode("utf-8")
        if len(encoded) > max_bytes:
            raise ValueError("修改后的文件超过单次写入预算")
        self.write(relative, encoded, overwrite=True)
        return {
            "path": relative,
            "replacements": occurrences if replace_all else 1,
            "bytes": len(encoded),
        }

    def search(
        self,
        query: str = "",
        *,
        relative: str = "",
        glob_pattern: str = "*",
        regex: bool = False,
        case_sensitive: bool = False,
        include_hidden: bool = False,
        max_results: int = 100,
    ) -> JsonObject:
        base = self.path(relative, allow_root=True)
        if not base.exists() and base == self.root:
            return {
                "mode": "content" if query else "files",
                "matches": [],
                "total": 0,
                "omitted": 0,
                "truncated": False,
            }
        if not base.is_dir():
            raise ValueError("搜索起始目录不存在")
        cap = max(1, min(max_results, 500))
        pattern_text = glob_pattern.strip() or "*"
        has_slash = "/" in pattern_text
        compiled: re.Pattern[str] | None = None
        if query:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                compiled = re.compile(query if regex else re.escape(query), flags)
            except re.error as exc:
                raise ValueError(f"正则表达式无效：{exc}") from None

        file_candidates: list[tuple[float, str, int]] = []
        content_matches: list[JsonValue] = []
        total_matches = 0

        for dirpath, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
            current_dir = Path(dirpath)
            kept_dirs: list[str] = []
            for dname in sorted(dirnames):
                child = current_dir / dname
                if child.is_symlink():
                    continue
                if not include_hidden and (dname.startswith(".") or dname in SKIP_SEARCH_DIRS):
                    continue
                kept_dirs.append(dname)
            dirnames[:] = kept_dirs

            for fname in sorted(filenames):
                if not include_hidden and fname.startswith("."):
                    continue
                fpath = current_dir / fname
                try:
                    st = fpath.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                rel_base = fpath.relative_to(base).as_posix()
                matched_glob = (
                    PurePosixPath(rel_base).full_match(pattern_text)
                    if has_slash
                    else fnmatch.fnmatchcase(
                        fname if case_sensitive else fname.casefold(),
                        pattern_text if case_sensitive else pattern_text.casefold(),
                    )
                )
                if not matched_glob:
                    continue
                rel_root = fpath.relative_to(self.root).as_posix()
                if compiled is None:
                    file_candidates.append((st.st_mtime, rel_root, st.st_size))
                    continue
                if st.st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                try:
                    with fpath.open("rb") as stream:
                        sample = stream.read(8192)
                        if b"\x00" in sample:
                            continue
                        raw = sample + stream.read(MAX_SEARCH_FILE_BYTES - len(sample))
                except OSError:
                    continue
                for lineno, line in enumerate(
                    raw.decode("utf-8", errors="replace").splitlines(), 1
                ):
                    if compiled.search(line):
                        total_matches += 1
                        if len(content_matches) < cap:
                            preview = line if len(line) <= 240 else line[:240] + "…"
                            content_matches.append(
                                {"path": rel_root, "line": lineno, "text": preview}
                            )

        if compiled is None:
            file_candidates.sort(key=lambda item: (-item[0], item[1]))
            total_files = len(file_candidates)
            kept = [
                {"path": rel, "size": size, "mtime": round(mtime, 3)}
                for mtime, rel, size in file_candidates[:cap]
            ]
            return JSON_OBJECT.validate_python(
                {
                    "mode": "files",
                    "matches": kept,
                    "total": total_files,
                    "omitted": max(0, total_files - len(kept)),
                    "truncated": total_files > cap,
                }
            )
        return JSON_OBJECT.validate_python(
            {
                "mode": "content",
                "matches": content_matches,
                "total": total_matches,
                "omitted": max(0, total_matches - len(content_matches)),
                "truncated": total_matches > cap,
            }
        )

    def delete(self, relative: str, *, recursive: bool = False) -> JsonObject:
        normalized = PurePosixPath(relative).as_posix()
        if normalized in RESERVED_ROOTS:
            raise ValueError("不能删除系统保留的基础目录本身")
        target = self.path(relative, allow_root=False, allow_leaf_symlink=True)
        if target.is_symlink():
            target.unlink()
            self.invalidate_usage()
            return {"removed": normalized}
        if not target.exists():
            raise FileNotFoundError(errno.ENOENT, "文件或目录不存在", relative)
        if target.is_dir():
            if not recursive and any(target.iterdir()):
                raise ValueError("目录非空；如需连同内部文件删除请设置 recursive=true")
            _remove_tree_force(target)
        else:
            target.unlink()
        self.invalidate_usage()
        return {"removed": normalized}

    def make_parents(self, relative: str) -> None:
        destination = self.path(relative)
        parent = self.root
        for component in destination.parent.relative_to(self.root).parts:
            parent = parent / component
            if not parent.exists():
                parent.mkdir(mode=0o700)

    def create_exec_tmp(self, execution_id: str) -> Path:
        self.prepare_dirs()
        rel = f".tmp/exec-{execution_id}"
        target = self.path(rel)
        target.mkdir(mode=0o700, exist_ok=True)
        return target

    def remove_exec_tmp(self, execution_id: str) -> None:
        rel = f".tmp/exec-{execution_id}"
        try:
            target = self.path(rel, allow_leaf_symlink=True)
        except ValueError:
            return
        if target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.exists():
            shutil.rmtree(target, ignore_errors=True)
        self.invalidate_usage()

    def prune_outputs(self, max_files: int = MAX_OUTPUT_FILES) -> None:
        outputs_dir = self.root / ".outputs"
        if not outputs_dir.is_dir() or outputs_dir.is_symlink():
            return
        entries: list[tuple[float, Path]] = []
        for item in outputs_dir.iterdir():
            try:
                st = item.lstat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                entries.append((st.st_mtime, item))
        if len(entries) <= max_files:
            return
        entries.sort(key=lambda pair: pair[0])
        for _, old_path in entries[: len(entries) - max_files]:
            old_path.unlink(missing_ok=True)
        self.invalidate_usage()

    def cleanup(self, mode: str, paths: list[str]) -> list[str]:
        self.root.mkdir(parents=True, exist_ok=True)
        if mode == "temp":
            targets = [".tmp", ".scratch", ".outputs"]
        elif mode == "cache":
            targets = [".cache"]
        elif mode == "all":
            targets = [p.name for p in self.root.iterdir()]
        elif mode == "selected":
            targets = paths
        else:
            raise ValueError("未知清理范围")
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
                _remove_tree_force(path)
            else:
                path.unlink()
            removed.append(name)
        if mode in ("temp", "cache"):
            self.prepare_dirs()
        self.invalidate_usage()
        return removed

    def prepare_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for relative in RESERVED_ROOTS:
            path = self.path(relative)
            path.mkdir(mode=0o700, exist_ok=True)

    def prepare(self, *, clean_stale_exec_tmp: bool = False) -> None:
        self.prepare_dirs()
        if clean_stale_exec_tmp:
            tmp_root = self.path(".tmp")
            for child in tmp_root.iterdir():
                if child.name.startswith("exec-"):
                    if child.is_symlink():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
        self.invalidate_usage()
