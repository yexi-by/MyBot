"""WebUI 配置读写：tomlkit 保注释写回、dry-run 校验与乐观锁。"""

import hashlib
import os
import stat
import tempfile
import tomllib
from collections.abc import MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, cast

import tomlkit
from pydantic import ValidationError

from app.config import (
    RESTART_ONLY_SECTIONS,
    ConfigIssue,
    ConfigLoadError,
    MyBotConfig,
    safe_validation_issues,
    validate_config_model,
)


class ConfigConflictError(RuntimeError):
    """目标文件在读取之后已被外部修改。"""


_WRITE_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class ConfigReadResult:
    """一次配置文件读取的完整结果。"""

    config: dict[str, Any]
    sha256: str
    valid: bool
    issues: tuple[ConfigIssue, ...]
    parsed: MyBotConfig | None


@dataclass(frozen=True, slots=True)
class ConfigWriteResult:
    """一次配置写回的结果。"""

    sha256: str
    restart_required_sections: tuple[str, ...]


def sha256_text(text: str) -> str:
    """返回 UTF-8 文本的内容哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def serialized_file_write() -> Iterator[None]:
    """串行处理当前进程内的配置和文本文件比较写入。"""
    with _WRITE_LOCK:
        yield


def atomic_write_text(path: Path, content: str) -> None:
    """临时文件加原子替换写入，避免 watcher 读到半个文件。"""
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(
            file_descriptor,
            mode="w",
            encoding="utf-8",
            newline="",
        ) as temporary_file:
            _ = temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        if path.exists():
            os.chmod(temporary_path, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_and_validate(*, config_file: Path, payload: dict[str, Any]) -> MyBotConfig:
    """完整校验 WebUI 提交的配置 JSON；失败抛出脱敏的 ConfigLoadError。"""
    try:
        config = MyBotConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigLoadError(safe_validation_issues(exc)) from exc
    _ = validate_config_model(config=config, config_file=config_file)
    return config


def restart_sections(
    *, boot_config: MyBotConfig, candidate: MyBotConfig
) -> tuple[str, ...]:
    """返回候选配置相对启动配置发生变化的待重启节。"""
    return tuple(
        section
        for section in RESTART_ONLY_SECTIONS
        if getattr(candidate, section) != getattr(boot_config, section)
    )


def read_config_payload(*, config_file: Path) -> ConfigReadResult:
    """读取原始 TOML 内容并返回哈希与校验状态；文件非法时仍可展示修复。"""
    text = config_file.read_text(encoding="utf-8")
    digest = sha256_text(text)
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return ConfigReadResult(
            config={},
            sha256=digest,
            valid=False,
            issues=(ConfigIssue(str(config_file), "toml_decode", str(exc)),),
            parsed=None,
        )
    try:
        parsed = parse_and_validate(config_file=config_file, payload=raw)
    except ConfigLoadError as exc:
        return ConfigReadResult(
            config=raw,
            sha256=digest,
            valid=False,
            issues=exc.issues,
            parsed=None,
        )
    return ConfigReadResult(
        config=raw,
        sha256=digest,
        valid=True,
        issues=(),
        parsed=parsed,
    )


def _merge_table(table: MutableMapping[str, Any], payload: dict[str, Any]) -> None:
    """把 payload 深合并进 tomlkit 表，保留未触及的注释与格式。

    payload 是完整配置视图：文档中存在而 payload 缺失（或为 None）的键被删除，
    数组与标量整体替换，嵌套表递归更新以保留 inline 形态。
    """
    for key in list(table.keys()):
        if key not in payload or payload[key] is None:
            del table[key]
    for key, value in payload.items():
        if value is None:
            continue
        current = table.get(key)
        if isinstance(value, dict) and isinstance(current, MutableMapping):
            _merge_table(
                cast("MutableMapping[str, Any]", current),
                cast("dict[str, Any]", value),
            )
        else:
            table[key] = value


def write_config_payload(
    *,
    config_file: Path,
    payload: dict[str, Any],
    base_sha256: str,
    boot_config: MyBotConfig,
) -> ConfigWriteResult:
    """校验通过后用 tomlkit 在原文件基础上写回，保留注释与格式。"""
    with serialized_file_write():
        current_text = config_file.read_text(encoding="utf-8")
        if sha256_text(current_text) != base_sha256:
            raise ConfigConflictError("配置文件已被外部修改，请刷新后重试")
        candidate = parse_and_validate(config_file=config_file, payload=payload)
        document = tomlkit.parse(current_text)
        _merge_table(document, payload)
        new_text = tomlkit.dumps(document)
        atomic_write_text(config_file, new_text)
    return ConfigWriteResult(
        sha256=sha256_text(new_text),
        restart_required_sections=restart_sections(
            boot_config=boot_config, candidate=candidate
        ),
    )
