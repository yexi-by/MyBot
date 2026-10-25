"""异步重试工具。"""

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.utils.log import log_event


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """重试前的日志回调"""
    if retry_state.outcome is None:
        return

    if retry_state.outcome.failed:
        exc = retry_state.outcome.exception()
        log_event(
            level="DEBUG",
            event="retry.before_sleep",
            category="retry",
            message="操作失败，即将重试",
            attempt=retry_state.attempt_number,
            error_type=type(exc).__name__ if exc is not None else "unknown",
        )
    else:
        log_event(
            level="DEBUG",
            event="retry.before_sleep",
            category="retry",
            message="结果校验失败，即将重试",
            attempt=retry_state.attempt_number,
        )


def create_retry_manager(
    *,
    error_types: tuple[type[Exception], ...],
    max_attempts: int,
    retry_delay_seconds: float,
    retry_max_delay_seconds: float,
) -> AsyncRetrying:
    """创建一个异步重试管理器，用于在操作失败时自动进行重试。

    支持基于异常类型的重试。
    使用指数退避策略控制重试间隔。

    Args:
        error_types: 需要触发重试的异常类型元组。当捕获到这些异常时会自动重试。
        max_attempts: 包含首次请求的最大尝试次数。
        retry_delay_seconds: 初始重试延迟时间（秒）。
        retry_max_delay_seconds: 指数退避最大等待秒数；0 表示不另设上限。

    Returns:
        配置好的 AsyncRetrying 实例，可用于异步函数的重试控制。

    Example:
        >>> retry_manager = create_retry_manager(
        ...     error_types=(ConnectionError, TimeoutError),
        ...     max_attempts=5,
        ...     retry_delay_seconds=1,
        ...     retry_max_delay_seconds=0,
        ... )
        >>> async for attempt in retry_manager:
        ...     with attempt:
        ...         result = await some_async_operation()
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 必须大于等于 1")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds 不能小于 0")
    if retry_max_delay_seconds < 0:
        raise ValueError("retry_max_delay_seconds 不能小于 0")
    if 0 < retry_max_delay_seconds < retry_delay_seconds:
        raise ValueError("retry_max_delay_seconds 不能小于初始重试延迟")
    retry_strategy = retry_if_exception_type(error_types)
    return AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(
            multiplier=retry_delay_seconds,
            min=retry_delay_seconds,
            max=(
                retry_max_delay_seconds
                if retry_max_delay_seconds > 0
                else float("inf")
            ),
        ),
        retry=retry_strategy,
        reraise=True,
        before_sleep=_log_retry_attempt,
    )
