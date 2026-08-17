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
from pydantic import SecretStr, ValidationError

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


class _MissingValue:
    """表示候选配置相对当前有效配置没有变化。"""


_MISSING_VALUE = _MissingValue()


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

    config: dict[str, Any]
    sha256: str
    restart_required_sections: tuple[str, ...]


def sha256_text(text: str) -> str:
    """返回 UTF-8 文本的内容哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain_config_value(value: object) -> Any:
    """把配置模型导出的 Python 值转换为 WebUI 可编辑的明文 JSON 值。"""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        items = cast("dict[object, object]", value)
        return {
            str(key): _plain_config_value(item) for key, item in items.items()
        }
    if isinstance(value, (list, tuple)):
        items = cast("list[object] | tuple[object, ...]", value)
        return [_plain_config_value(item) for item in items]
    return value


def materialize_config_payload(config: MyBotConfig) -> dict[str, Any]:
    """展开所有默认值，并仅为内网 WebUI 还原可编辑的密钥明文。"""
    payload = _plain_config_value(
        config.model_dump(mode="python", exclude_none=True)
    )
    if not isinstance(payload, dict):
        raise TypeError("MyBotConfig 导出结果不是对象")
    return cast("dict[str, Any]", payload)


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
        config=materialize_config_payload(parsed),
        sha256=digest,
        valid=True,
        issues=(),
        parsed=parsed,
    )


def _changed_value(
    candidate: Any,
    baseline: Any,
) -> Any | _MissingValue:
    """提取候选值相对当前有效值的最小变化。"""
    if isinstance(candidate, dict) and isinstance(baseline, dict):
        candidate_items = cast("dict[str, Any]", candidate)
        baseline_items = cast("dict[str, Any]", baseline)
        changed: dict[str, Any] = {}
        for key, value in candidate_items.items():
            if key not in baseline_items:
                changed[key] = value
                continue
            nested = _changed_value(value, baseline_items[key])
            if nested is not _MISSING_VALUE:
                changed[key] = nested
        if changed:
            return changed
        return _MISSING_VALUE
    if candidate != baseline:
        return cast("Any", candidate)
    return _MISSING_VALUE


def _merge_table(
    table: MutableMapping[str, Any],
    payload: dict[str, Any],
    baseline: dict[str, Any],
) -> None:
    """把 payload 深合并进 tomlkit 表，保留未触及的注释与格式。

    payload 是完整配置视图：文档中存在而 payload 缺失（或为 None）的键被删除，
    数组与标量整体替换，嵌套表递归更新以保留 inline 形态。原文件没有且
    仍采用模型默认值的字段不会被展开写入。
    """
    for key in list(table.keys()):
        if key not in payload or payload[key] is None:
            del table[key]
    for key, value in payload.items():
        if value is None:
            continue
        current = table.get(key)
        if isinstance(value, dict) and isinstance(current, MutableMapping):
            baseline_value = baseline.get(key)
            _merge_table(
                cast("MutableMapping[str, Any]", current),
                cast("dict[str, Any]", value),
                (
                    cast("dict[str, Any]", baseline_value)
                    if isinstance(baseline_value, dict)
                    else {}
                ),
            )
        elif key in table:
            if key not in baseline:
                table[key] = value
            elif _changed_value(value, baseline[key]) is not _MISSING_VALUE:
                table[key] = value
        elif key not in baseline:
            table[key] = value
        else:
            changed = _changed_value(value, baseline[key])
            if changed is not _MISSING_VALUE:
                table[key] = changed


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
        normalized_payload = materialize_config_payload(candidate)
        try:
            current_config = parse_and_validate(
                config_file=config_file,
                payload=tomllib.loads(current_text),
            )
        except ConfigLoadError:
            baseline_payload: dict[str, Any] = {}
        else:
            baseline_payload = materialize_config_payload(current_config)
        document = tomlkit.parse(current_text)
        _merge_table(document, normalized_payload, baseline_payload)
        new_text = tomlkit.dumps(document)
        atomic_write_text(config_file, new_text)
    return ConfigWriteResult(
        config=normalized_payload,
        sha256=sha256_text(new_text),
        restart_required_sections=restart_sections(
            boot_config=boot_config, candidate=candidate
        ),
    )
