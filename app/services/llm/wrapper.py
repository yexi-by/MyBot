from openai import APIConnectionError, APITimeoutError, RateLimitError

from app.utils import create_retry_manager

from .base import LLMProvider
from .schemas import ChatMessage, LLMConfig


class ResilientLLMProvider(LLMProvider):
    def __init__(self, inner_provider: LLMProvider, llm_config: LLMConfig):
        self.inner_provider = inner_provider
        self.llm_config = llm_config

    async def get_ai_response(
        self, messages: list[ChatMessage], model: str, **kwargs
    ) -> str:
        retry_count = self.llm_config.retry_count
        retry_delay = self.llm_config.retry_delay
        retrier = create_retry_manager(
            retry_count=retry_count,
            retry_delay=retry_delay,
            error_types=(
                RateLimitError,
                APIConnectionError,
                APITimeoutError,
                ValueError,
            ),
        )
        async for attempt in retrier:
            with attempt:
                response = await self.inner_provider.get_ai_response(
                    messages=messages, model=model, **kwargs
                )
                if not response:
                    raise ValueError("LLM 提供商返回了空响应")
                return response
        raise RuntimeError("Retries exhausted")  # 规避下类型检查,这行是死代码

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        retry_count = self.llm_config.retry_count
        retry_delay = self.llm_config.retry_delay
        retrier = create_retry_manager(
            retry_count=retry_count,
            retry_delay=retry_delay,
            error_types=(
                RateLimitError,
                APIConnectionError,
                APITimeoutError,
                ValueError,
            ),
        )
        async for attempt in retrier:
            with attempt:
                response = await self.inner_provider.get_image(
                    message=message, model=model
                )
                if not response:
                    raise ValueError("LLM 提供商返回了空图片响应")
                return response
        raise RuntimeError("Retries exhausted")  # 规避下类型检查,这行是死代码
