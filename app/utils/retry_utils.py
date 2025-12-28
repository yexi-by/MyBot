from typing import Callable, Optional, Type
from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_result,
)


def create_retry_manager(
    retry_count: int,
    retry_delay: int,
    error_types: tuple[Type[Exception], ...] = (Exception,),
    custom_checker: Optional[Callable[..., bool]] = None,
) -> AsyncRetrying:
    retry_strategy = retry_if_exception_type(error_types)
    if custom_checker:
        retry_strategy = retry_strategy | retry_if_result(custom_checker)
    return AsyncRetrying(
        stop=stop_after_attempt(retry_count),
        wait=wait_exponential(multiplier=1, min=retry_delay, max=10),
        retry=retry_strategy,
        reraise=True,
    )
