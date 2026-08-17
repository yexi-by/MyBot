"""config/ 目录内文本文件（prompt、知识库等）的列表与读写。"""

from pathlib import Path, PurePosixPath

from app.config import ConfigIssue, ConfigLoadError, resolve_config_file

from .config_io import (
    ConfigConflictError,
    atomic_write_text,
    serialized_file_write,
    sha256_text,
)

_TEXT_FILE_SUFFIXES = frozenset({".md", ".txt"})


def _validate_text_relative_path(relative_path: str) -> None:
    """文本 API 只接受 config/ 内使用正斜杠表示的 md/txt 文件。"""
    candidate = PurePosixPath(relative_path)
    if (
        relative_path.strip() == ""
        or "\\" in relative_path
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ConfigLoadError(
            (ConfigIssue("file", "invalid_path", "文件路径必须是 config 目录内的相对路径"),)
        )
    if candidate.suffix.lower() not in _TEXT_FILE_SUFFIXES:
        raise ConfigLoadError(
            (ConfigIssue("file", "unsupported_file_type", "只允许读写 .md 或 .txt 文件"),)
        )


def list_text_files(*, config_root: Path) -> list[str]:
    """递归收集 config/ 内的可编辑文本文件，返回正斜杠相对路径。"""
    root = config_root.resolve(strict=True)
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _TEXT_FILE_SUFFIXES:
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            continue
        files.append(path.relative_to(root).as_posix())
    return files


def read_text_file(*, config_root: Path, relative_path: str) -> tuple[str, str]:
    """读取单个文本文件并返回内容与哈希；逃逸与缺失抛 ConfigLoadError。"""
    _validate_text_relative_path(relative_path)
    path = resolve_config_file(
        config_root=config_root, file_value=relative_path, label="file"
    )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigLoadError(
            (ConfigIssue("file", type(exc).__name__, "文件不是可读取的 UTF-8 文本"),)
        ) from exc
    return content, sha256_text(content)


def write_text_file(
    *,
    config_root: Path,
    relative_path: str,
    content: str,
    base_sha256: str | None,
) -> str:
    """写回文本文件；已存在文件强制乐观锁，返回新内容哈希。"""
    _validate_text_relative_path(relative_path)
    path = resolve_config_file(
        config_root=config_root,
        file_value=relative_path,
        label="file",
        must_exist=False,
    )
    with serialized_file_write():
        if path.exists():
            try:
                current = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ConfigLoadError(
                    (
                        ConfigIssue(
                            "file",
                            type(exc).__name__,
                            "现有文件不是可读取的 UTF-8 文本",
                        ),
                    )
                ) from exc
            if base_sha256 is None or sha256_text(current) != base_sha256:
                raise ConfigConflictError("文件已被外部修改，请刷新后重试")
        atomic_write_text(path, content)
    return sha256_text(content)
