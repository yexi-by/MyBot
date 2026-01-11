from typing import Type

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    RetryCallState
)

from app.utils.log import logger


def _log_retry_attempt(retry_state: RetryCallState):
    """重试前的日志回调"""
    if retry_state.outcome is None:
        return

    if retry_state.outcome.failed:
        exc = retry_state.outcome.exception()
        logger.opt(exception=exc).debug(f"重试第 {retry_state.attempt_number} 次，错误: {exc}")
    else:
        logger.debug(f"重试第 {retry_state.attempt_number} 次，结果校验失败: {retry_state.outcome.result()}")


def create_retry_manager(
    error_types: tuple[Type[Exception], ...] = (Exception,),
    retry_count: int = 10,
    retry_delay: int = 2,
) -> AsyncRetrying:
    """创建一个异步重试管理器，用于在操作失败时自动进行重试。

    支持基于异常类型的重试。
    使用指数退避策略控制重试间隔。

    Args:
        error_types: 需要触发重试的异常类型元组。当捕获到这些异常时会自动重试。
        retry_count: 最大重试次数。
        retry_delay: 初始重试延迟时间（秒），实际延迟会按指数增长，最大不超过 10 秒。

    Returns:
        配置好的 AsyncRetrying 实例，可用于异步函数的重试控制。

    Example:
        >>> retry_manager = create_retry_manager(
        ...     error_types=(ConnectionError, TimeoutError),
        ...     retry_count=5,
        ...     retry_delay=1
        ... )
        >>> async for attempt in retry_manager:
        ...     with attempt:
        ...         result = await some_async_operation()
    """
            
    retry_strategy = retry_if_exception_type(error_types)
    return AsyncRetrying(
        stop=stop_after_attempt(retry_count),
        wait=wait_exponential(multiplier=1, min=retry_delay, max=10),
        retry=retry_strategy,
        reraise=True,
        before_sleep=_log_retry_attempt,
    )
