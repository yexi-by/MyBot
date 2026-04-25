"""项目统一日志门面。

业务代码只通过本模块暴露的事件函数写日志，日志层统一负责字段渲染、
终端视图、文件视图和异常链落盘。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger as _logger

from app.models.common import JsonValue

if TYPE_CHECKING:
    from loguru import Logger, Record

LogLevel = Literal["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"]

DEFAULT_LOG_DIR_NAME = "logs"
APP_LOG_PATTERN = "{time:YYYY-MM-DD}_app.log"
ERROR_LOG_PATTERN = "{time:YYYY-MM-DD}_error.log"
STRUCTURED_LOG_PATTERN = "{time:YYYY-MM-DD}_structured.json"

_log_dir = Path(DEFAULT_LOG_DIR_NAME)
_console_level = os.getenv("LOG_CONSOLE_LEVEL", "INFO")
_file_level = os.getenv("LOG_FILE_LEVEL", "DEBUG")
_log_retention = os.getenv("LOG_RETENTION", "30 days")
_log_rotation = os.getenv("LOG_ROTATION", "50 MB")
_log_compression = os.getenv("LOG_COMPRESSION", "gz")
_logger_configured = False

CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[category]}</cyan> | "
    "<level>{message}</level>"
    "<dim>{extra[fields_text]}</dim>"
)

FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "event={extra[event]} | "
    "category={extra[category]} | "
    "{message}"
    "{extra[fields_text]}"
)


def format_log_fields(fields: dict[str, JsonValue]) -> str:
    """把结构化字段渲染成稳定、可扫读的日志后缀。"""
    if not fields:
        return ""
    parts = [f"{key}={value}" for key, value in fields.items()]
    return " | " + " ".join(parts)


def _console_filter(record: "Record") -> bool:
    """隐藏只应落盘的完整异常日志。"""
    raw_target = record["extra"].get("target")
    if not isinstance(raw_target, str):
        return True
    return raw_target != "file_only"


def configure_logging(
    *,
    log_dir: str = DEFAULT_LOG_DIR_NAME,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    retention: str = "30 days",
    rotation: str = "50 MB",
    compression: str = "gz",
) -> None:
    """按配置初始化终端日志、文本文件日志和结构化日志。"""
    global _console_level
    global _file_level
    global _log_compression
    global _log_dir
    global _log_retention
    global _log_rotation
    global _logger_configured

    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)
    _console_level = console_level
    _file_level = file_level
    _log_retention = retention
    _log_rotation = rotation
    _log_compression = compression
    _configure_logger()
    _logger_configured = True
    log_event(
        level="INFO",
        event="log.initialized",
        category="runtime",
        message="日志系统初始化完成",
        log_dir=str(_log_dir.absolute()),
        console_level=_console_level,
        file_level=_file_level,
    )


def _ensure_logger_configured() -> None:
    """在测试或脚本直接调用日志门面时启用默认日志配置。"""
    if _logger_configured:
        return
    configure_logging(
        log_dir=DEFAULT_LOG_DIR_NAME,
        console_level=os.getenv("LOG_CONSOLE_LEVEL", "INFO"),
        file_level=os.getenv("LOG_FILE_LEVEL", "DEBUG"),
        retention=os.getenv("LOG_RETENTION", "30 days"),
        rotation=os.getenv("LOG_ROTATION", "50 MB"),
        compression=os.getenv("LOG_COMPRESSION", "gz"),
    )


def _configure_logger() -> "Logger":
    """配置终端、文本文件和结构化文件三类日志 sink。"""
    _logger.remove()
    _ = _logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=_console_level,
        colorize=True,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        filter=_console_filter,
    )
    _ = _logger.add(
        _log_dir / APP_LOG_PATTERN,
        format=FILE_FORMAT,
        level=_file_level,
        rotation=_log_rotation,
        retention=_log_retention,
        compression=_log_compression,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    _ = _logger.add(
        _log_dir / ERROR_LOG_PATTERN,
        format=FILE_FORMAT,
        level="ERROR",
        rotation=_log_rotation,
        retention=_log_retention,
        compression=_log_compression,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )
    _ = _logger.add(
        _log_dir / STRUCTURED_LOG_PATTERN,
        level=_file_level,
        rotation=_log_rotation,
        retention=_log_retention,
        compression=_log_compression,
        encoding="utf-8",
        enqueue=True,
        serialize=True,
    )
    return _logger


logger: Logger = _logger


def log_event(
    *,
    level: LogLevel,
    event: str,
    category: str,
    message: str,
    **fields: JsonValue,
) -> None:
    """记录一条结构化业务事件日志。"""
    _ensure_logger_configured()
    bound_logger = logger.bind(
        event=event,
        category=category,
        fields=fields,
        fields_text=format_log_fields(fields),
    )
    bound_logger.log(level, message)


def log_exception(
    *,
    event: str,
    category: str,
    message: str,
    exc: BaseException,
    **fields: JsonValue,
) -> None:
    """记录异常摘要到终端，并把完整异常链写入文件日志。"""
    _ensure_logger_configured()
    log_event(
        level="ERROR",
        event=event,
        category=category,
        message=message,
        error_type=type(exc).__name__,
        error=str(exc),
        **fields,
    )
    logger.bind(
        event=f"{event}.traceback",
        category=category,
        fields=fields,
        fields_text=format_log_fields(fields),
        target="file_only",
    ).opt(exception=exc).error(message)


def log_run_start(*, message: str, **fields: JsonValue) -> None:
    """记录一次应用运行开始事件。"""
    log_event(
        level="INFO",
        event="app.run.start",
        category="runtime",
        message=message,
        log_dir=str(_log_dir.absolute()),
        console_level=_console_level,
        file_level=_file_level,
        **fields,
    )


def log_run_end(*, message: str, **fields: JsonValue) -> None:
    """记录一次应用运行结束事件。"""
    log_event(
        level="SUCCESS",
        event="app.run.end",
        category="runtime",
        message=message,
        **fields,
    )


__all__ = [
    "DEFAULT_LOG_DIR_NAME",
    "LogLevel",
    "configure_logging",
    "format_log_fields",
    "log_event",
    "log_exception",
    "log_run_end",
    "log_run_start",
    "logger",
]
