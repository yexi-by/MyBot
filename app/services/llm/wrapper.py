"""LLM 提供商重试包装器。"""

from typing import override

from openai import APIConnectionError, InternalServerError, RateLimitError

from app.config.schemas import LLMProviderConfig
from app.utils.retry_utils import create_retry_manager

from .base import LLMProvider
from .errors import LLMResponseError
from .schemas import (
    ChatMessage,
    LLMResponse,
    LLMToolChoice,
    LLMToolDefinition,
)


RETRYABLE_ERRORS = (APIConnectionError, InternalServerError, RateLimitError, TimeoutError)


class ResilientLLMProvider(LLMProvider):
    """为底层 LLM 提供商增加统一重试能力。"""

    def __init__(
        self, inner_provider: LLMProvider, provider_config: LLMProviderConfig
    ) -> None:
        """保存底层服务商与重试配置。"""
        self.inner_provider: LLMProvider = inner_provider
        self.provider_config: LLMProviderConfig = provider_config

    @override
    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> str:
        """按配置重试网络与暂时服务错误，重试时保持原始请求不变。"""
        retrier = create_retry_manager(
            max_attempts=self.provider_config.max_attempts,
            retry_delay_seconds=self.provider_config.retry_delay_seconds,
            retry_max_delay_seconds=self.provider_config.retry_max_delay_seconds,
            error_types=RETRYABLE_ERRORS,
        )
        async for attempt in retrier:
            with attempt:
                response = await self.inner_provider.get_ai_response(
                    messages=messages, model=model
                )
                if not response.strip():
                    raise LLMResponseError("empty_response", "LLM 提供商返回了空响应")
                return response
        raise RuntimeError("LLM 文本接口重试次数已耗尽")

    @override
    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """调用底层工具接口，只重试网络与暂时服务错误。"""
        retrier = create_retry_manager(
            max_attempts=self.provider_config.max_attempts,
            retry_delay_seconds=self.provider_config.retry_delay_seconds,
            retry_max_delay_seconds=self.provider_config.retry_max_delay_seconds,
            error_types=RETRYABLE_ERRORS,
        )
        async for attempt in retrier:
            with attempt:
                response = await self.inner_provider.get_ai_response_with_tools(
                    messages=messages,
                    model=model,
                    tools=tools,
                    tool_choice=tool_choice,
                    parallel_tool_calls=parallel_tool_calls,
                )
                if not (response.content or "").strip() and not response.tool_calls:
                    raise LLMResponseError("empty_response", "LLM 提供商返回了空工具响应")
                return response
        raise RuntimeError("LLM 工具接口重试次数已耗尽")
