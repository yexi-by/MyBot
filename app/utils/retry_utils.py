from typing import Callable, Optional, Type

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)


def create_retry_manager(
    error_types: tuple[Type[Exception], ...] = (Exception,),
    custom_checker: Optional[Callable[..., bool]] = None,
    retry_count: int = 10,
    retry_delay: int = 2,
) -> AsyncRetrying:
    """创建一个异步重试管理器，用于在操作失败时自动进行重试。

    支持基于异常类型的重试，也可以通过自定义检查函数来判断返回结果是否需要重试。
    使用指数退避策略控制重试间隔。

    Args:
        error_types: 需要触发重试的异常类型元组。当捕获到这些异常时会自动重试。
        custom_checker: 自定义结果检查函数。接收函数返回值，返回 True 表示需要重试。
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
    if custom_checker:
        retry_strategy = retry_strategy | retry_if_result(custom_checker)
    return AsyncRetrying(
        stop=stop_after_attempt(retry_count),
        wait=wait_exponential(multiplier=1, min=retry_delay, max=10),
        retry=retry_strategy,
        reraise=True,
    )
