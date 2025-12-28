"""
日志配置模块 - 基于 Loguru 的生产级日志系统

提供统一的日志管理，支持：
- 控制台彩色输出
- 文件日志轮转
- 结构化日志
- 异常追踪增强
- 模块级别日志器

使用方法:
    from log import logger
    logger.info("Hello World")

    # 或者获取模块专用日志器
    from log import get_logger
    log = get_logger("my_module")
    log.info("模块日志")
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as _logger

if TYPE_CHECKING:
    from loguru import Logger


# ================================
# 日志配置常量
# ================================

# 日志目录
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# 日志级别 (可通过环境变量覆盖)
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")

# 日志保留天数
LOG_RETENTION = os.getenv("LOG_RETENTION", "30 days")

# 单个日志文件最大大小
LOG_ROTATION = os.getenv("LOG_ROTATION", "50 MB")

# 是否启用 JSON 格式日志 (生产环境推荐)
LOG_JSON_FORMAT = os.getenv("LOG_JSON_FORMAT", "false").lower() == "true"


# ================================
# 日志格式定义
# ================================

# 控制台格式 - 带颜色，简洁美观
CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# 文件格式 - 详细信息，便于排查问题
FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{function}:{line} | "
    "{message}"
)

# JSON 格式字段 (用于生产环境日志聚合)
JSON_FORMAT = (
    '{{"timestamp": "{time:YYYY-MM-DDTHH:mm:ss.SSSZ}", '
    '"level": "{level}", '
    '"logger": "{name}", '
    '"function": "{function}", '
    '"line": {line}, '
    '"message": "{message}", '
    '"extra": {extra}}}'
)


# ================================
# 日志级别颜色配置
# ================================

LEVEL_COLORS = {
    "TRACE": "<dim>",
    "DEBUG": "<blue>",
    "INFO": "<green>",
    "SUCCESS": "<bold><green>",
    "WARNING": "<yellow>",
    "ERROR": "<red>",
    "CRITICAL": "<bold><red><WHITE>",
}


# ================================
# 日志过滤器
# ================================


def _level_filter(level: str):
    """创建日志级别过滤器"""

    def filter_func(record):
        return record["level"].no >= _logger.level(level).no

    return filter_func


def _module_filter(module_name: str):
    """创建模块过滤器"""

    def filter_func(record):
        return record["name"].startswith(module_name)

    return filter_func


# ================================
# 日志配置函数
# ================================


def _configure_logger() -> Logger:
    """
    配置并返回 logger 实例

    Returns:
        配置好的 Logger 实例
    """
    # 移除默认处理器
    _logger.remove()

    # ---- 控制台处理器 ----
    _logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=LOG_LEVEL,
        colorize=True,
        backtrace=True,  # 显示完整的异常回溯
        diagnose=True,  # 显示变量值 (生产环境建议关闭)
        enqueue=True,  # 异步写入，提高性能
    )

    # ---- 常规日志文件 ----
    _logger.add(
        LOG_DIR / "{time:YYYY-MM-DD}_app.log",
        format=FILE_FORMAT if not LOG_JSON_FORMAT else JSON_FORMAT,
        level=LOG_LEVEL,
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression="gz",  # 压缩旧日志
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,  # 文件日志关闭诊断信息
    )

    # ---- 错误日志文件 (单独记录 ERROR 及以上级别) ----
    _logger.add(
        LOG_DIR / "{time:YYYY-MM-DD}_error.log",
        format=FILE_FORMAT,
        level="ERROR",
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        compression="gz",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,  # 错误日志保留诊断信息
    )

    # ---- JSON 格式日志 (用于日志聚合系统) ----
    if LOG_JSON_FORMAT:
        _logger.add(
            LOG_DIR / "{time:YYYY-MM-DD}_structured.json",
            format=JSON_FORMAT,
            level=LOG_LEVEL,
            rotation=LOG_ROTATION,
            retention=LOG_RETENTION,
            compression="gz",
            encoding="utf-8",
            enqueue=True,
            serialize=True,  # 启用 JSON 序列化
        )

    return _logger


# ================================
# 公开接口
# ================================

# 配置并导出主 logger
logger = _configure_logger()


@lru_cache(maxsize=128)
def get_logger(name: str) -> Logger:
    """
    获取模块专用日志器

    Args:
        name: 模块名称，通常使用 __name__

    Returns:
        绑定了模块名称的 Logger 实例

    Example:
        >>> from log import get_logger
        >>> log = get_logger(__name__)
        >>> log.info("模块专用日志")
    """
    return logger.bind(name=name)


class LoggerContextManager:
    """
    日志上下文管理器，用于临时添加额外信息

    Example:
        >>> with LoggerContextManager(request_id="abc123", user_id=42):
        ...     logger.info("带上下文的日志")
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._context = None

    def __enter__(self):
        self._context = logger.contextualize(**self.kwargs)
        return self._context.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._context is not None:
            return self._context.__exit__(exc_type, exc_val, exc_tb)
        return None


def log_context(**kwargs):
    """
    日志上下文装饰器/上下文管理器

    Example:
        >>> @log_context(module="auth")
        ... def login():
        ...     logger.info("用户登录")

        >>> with log_context(request_id="abc"):
        ...     logger.info("处理请求")
    """
    return LoggerContextManager(**kwargs)


def setup_exception_handler():
    """
    设置全局异常处理器，确保未捕获的异常也被记录

    Example:
        >>> from log import setup_exception_handler
        >>> setup_exception_handler()
    """

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(
            "未捕获的异常"
        )

    sys.excepthook = handle_exception


def log_function_call(*, level: str = "DEBUG", include_result: bool = False):
    """
    函数调用日志装饰器

    Args:
        level: 日志级别
        include_result: 是否记录返回值

    Example:
        >>> @log_function_call(level="INFO", include_result=True)
        ... def process_data(data):
        ...     return data.upper()
    """

    def decorator(func):
        from functools import wraps

        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__qualname__
            logger.log(level, f"调用 {func_name} | args={args} kwargs={kwargs}")

            try:
                result = func(*args, **kwargs)
                if include_result:
                    logger.log(level, f"完成 {func_name} | result={result}")
                else:
                    logger.log(level, f"完成 {func_name}")
                return result
            except Exception as e:
                logger.exception(f"异常 {func_name} | error={e}")
                raise

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            func_name = func.__qualname__
            logger.log(level, f"调用 {func_name} | args={args} kwargs={kwargs}")

            try:
                result = await func(*args, **kwargs)
                if include_result:
                    logger.log(level, f"完成 {func_name} | result={result}")
                else:
                    logger.log(level, f"完成 {func_name}")
                return result
            except Exception as e:
                logger.exception(f"异常 {func_name} | error={e}")
                raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator


# ================================
# 初始化
# ================================

# 启动时记录一条日志
logger.info(f"日志系统初始化完成 | 级别={LOG_LEVEL} 目录={LOG_DIR.absolute()}")


# ================================
# 模块导出
# ================================

__all__ = [
    "logger",
    "get_logger",
    "log_context",
    "log_function_call",
    "setup_exception_handler",
    "LOG_DIR",
    "LOG_LEVEL",
]
