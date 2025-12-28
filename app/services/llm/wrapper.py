from .base import LLMProvider
from .schemas import LLMConfig, ChatMessage
from openai import APIConnectionError, APITimeoutError, RateLimitError
from utils import create_retry_manager

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
            error_types=(RateLimitError, APIConnectionError, APITimeoutError),
            custom_checker=lambda x: not x,
        )
        async for attempt in retrier:
            with attempt:
                response = await self.inner_provider.get_ai_response(
                    messages=messages, model=model, **kwargs
                )
                return response
        raise RuntimeError("Retries exhausted") # 规避下类型检查,这行是死代码