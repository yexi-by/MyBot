"""LLM 提供商重试包装器。"""

from typing import override

from openai import APIConnectionError, APITimeoutError, RateLimitError

from app.utils.retry_utils import create_retry_manager

from .base import LLMProvider
from .schemas import (
    ChatMessage,
    ImageGenerationOptions,
    LLMConfig,
    LLMResponse,
    LLMToolChoice,
    LLMToolDefinition,
)


class ResilientLLMProvider(LLMProvider):
    """为底层 LLM 提供商增加统一重试能力。"""

    def __init__(self, inner_provider: LLMProvider, llm_config: LLMConfig) -> None:
        """保存底层服务商与重试配置。"""
        self.inner_provider: LLMProvider = inner_provider
        self.llm_config: LLMConfig = llm_config

    @override
    async def get_ai_response(
        self, messages: list[ChatMessage], model: str
    ) -> str:
        """调用底层文本接口，并在可恢复错误时重试。"""
        retrier = create_retry_manager(
            retry_count=self.llm_config.retry_count,
            retry_delay=self.llm_config.retry_delay,
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
                    messages=messages, model=model
                )
                if not response:
                    raise ValueError("LLM 提供商返回了空响应")
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
        """调用底层工具接口，并在可恢复错误时重试。"""
        retrier = create_retry_manager(
            retry_count=self.llm_config.retry_count,
            retry_delay=self.llm_config.retry_delay,
            error_types=(
                RateLimitError,
                APIConnectionError,
                APITimeoutError,
                ValueError,
            ),
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
                if response.content is None and not response.tool_calls:
                    raise ValueError("LLM 提供商返回了空工具响应")
                return response
        raise RuntimeError("LLM 工具接口重试次数已耗尽")

    @override
    async def get_image(
        self,
        message: ChatMessage,
        model: str,
        options: ImageGenerationOptions | None = None,
    ) -> str:
        """调用底层图片接口，并在可恢复错误时重试。"""
        retrier = create_retry_manager(
            retry_count=self.llm_config.retry_count,
            retry_delay=self.llm_config.retry_delay,
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
                    message=message,
                    model=model,
                    options=options,
                )
                if not response:
                    raise ValueError("LLM 提供商返回了空图片响应")
                return response
        raise RuntimeError("LLM 图片接口重试次数已耗尽")
