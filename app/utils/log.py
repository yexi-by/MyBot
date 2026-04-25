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

LOG_DIR_NAME = "logs"
APP_LOG_PATTERN = "{time:YYYY-MM-DD}_app.log"
ERROR_LOG_PATTERN = "{time:YYYY-MM-DD}_error.log"
STRUCTURED_LOG_PATTERN = "{time:YYYY-MM-DD}_structured.json"

LOG_DIR = Path(LOG_DIR_NAME)
LOG_DIR.mkdir(exist_ok=True)

CONSOLE_LEVEL = os.getenv("LOG_CONSOLE_LEVEL", "INFO")
FILE_LEVEL = os.getenv("LOG_FILE_LEVEL", "DEBUG")
LOG_RETENTION = os.getenv("LOG_RETENTION", "30 days")
LOG_ROTATION = os.getenv("LOG_ROTATION", "50 MB")

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


def _configure_logger() -> "Logger":
    """配置终端、文本文件和结构化文件三类日志 sink。"""
    _logger.remove()
    _ = _logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=CONSOLE_LEVEL,
        colorize=True,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        filter=_console_filter,
    )
    _ = _logger.add(
        LOG_DIR / APP_LOG_PATTERN,
        format=FILE_FORMAT,
        level=FILE_LEVEL,
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression="gz",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    _ = _logger.add(
        LOG_DIR / ERROR_LOG_PATTERN,
        format=FILE_FORMAT,
        level="ERROR",
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression="gz",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )
    _ = _logger.add(
        LOG_DIR / STRUCTURED_LOG_PATTERN,
        level=FILE_LEVEL,
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression="gz",
        encoding="utf-8",
        enqueue=True,
        serialize=True,
    )
    return _logger


logger: Logger = _configure_logger()


def log_event(
    *,
    level: LogLevel,
    event: str,
    category: str,
    message: str,
    **fields: JsonValue,
) -> None:
    """记录一条结构化业务事件日志。"""
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
        log_dir=str(LOG_DIR.absolute()),
        console_level=CONSOLE_LEVEL,
        file_level=FILE_LEVEL,
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


log_event(
    level="INFO",
    event="log.initialized",
    category="runtime",
    message="日志系统初始化完成",
    log_dir=str(LOG_DIR.absolute()),
    console_level=CONSOLE_LEVEL,
    file_level=FILE_LEVEL,
)

__all__ = [
    "CONSOLE_LEVEL",
    "FILE_LEVEL",
    "LOG_DIR",
    "LogLevel",
    "format_log_fields",
    "log_event",
    "log_exception",
    "log_run_end",
    "log_run_start",
    "logger",
]
