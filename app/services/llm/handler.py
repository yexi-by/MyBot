"""LLM 服务注册与请求路由。"""

from typing import Self

import httpx
from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient

from app.config.schemas import LLMProviderConfig, NetworkConfig

from .errors import LLMRequestError, provider_error_message
from .providers.openai import OpenAIService
from .schemas import (
    ChatMessage,
    LLMProviderWrapper,
    LLMResponse,
    LLMToolChoice,
    LLMToolDefinition,
)
from .wrapper import ResilientLLMProvider


class LLMHandler:
    """按模型厂商路由到具体 LLM 服务。"""

    def __init__(
        self,
        services: dict[str, LLMProviderWrapper],
        clients: list[AsyncOpenAI],
    ) -> None:
        """保存按稳定 ID 注册的服务。"""
        self.services: dict[str, LLMProviderWrapper] = services
        self._clients: list[AsyncOpenAI] = clients

    @classmethod
    def register_instance(
        cls,
        providers: dict[str, LLMProviderConfig],
        network: NetworkConfig,
    ) -> Self:
        """根据配置注册 LLM 服务实例。"""
        services: dict[str, LLMProviderWrapper] = {}
        clients: list[AsyncOpenAI] = []
        for provider_id, provider_config in providers.items():
            api_key = (
                provider_config.api_key.get_secret_value()
                if provider_config.api_key is not None
                else ""
            )
            proxy = (
                provider_config.proxy
                if provider_config.proxy is not None
                else network.proxy if provider_config.inherit_network_proxy else None
            )
            timeout = (
                network.timeout_seconds
                if provider_config.timeout_seconds == 0
                else provider_config.timeout_seconds
            )
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=provider_config.base_url,
                timeout=timeout,
                max_retries=0,
                http_client=(
                    DefaultAsyncHttpxClient(proxy=proxy, timeout=timeout)
                    if proxy is not None
                    else None
                ),
            )
            clients.append(client)
            raw_service = OpenAIService(client=client)
            safe_service = ResilientLLMProvider(
                inner_provider=raw_service, provider_config=provider_config
            )
            wrapper = LLMProviderWrapper(
                provider_id=provider_id,
                provider=safe_service,
            )
            services[provider_id] = wrapper
        return cls(services=services, clients=clients)

    async def aclose(self) -> None:
        """关闭每个 provider 拥有的 OpenAI HTTP 客户端。"""
        for client in self._clients:
            await client.close()

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        provider: str,
        model_name: str,
        max_attempts: int | None = None,
        retry_delay_seconds: float | None = None,
        retry_max_delay_seconds: float | None = None,
    ) -> str:
        """获取指定模型厂商的文本响应，可覆盖当前请求的重试参数。"""
        llm = self.services.get(provider)
        if llm is None:
            raise ValueError(f"未定义的 LLM provider: {provider}")
        try:
            return await llm.provider.get_ai_response(
                messages=messages,
                model=model_name,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
                retry_max_delay_seconds=retry_max_delay_seconds,
            )
        except (APIError, httpx.RequestError, TimeoutError, ValueError) as exc:
            raise LLMRequestError(provider_error_message(exc)) from None

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        provider: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """获取指定模型厂商的工具调用结构化响应。"""
        llm = self.services.get(provider)
        if llm is None:
            raise ValueError(f"未定义的 LLM provider: {provider}")
        try:
            return await llm.provider.get_ai_response_with_tools(
                messages=messages,
                model=model_name,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
            )
        except (APIError, httpx.RequestError, TimeoutError, ValueError) as exc:
            raise LLMRequestError(provider_error_message(exc)) from None

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
        provider: str,
    ) -> str:
        """获取指定模型厂商的图片响应。"""
        llm = self.services.get(provider)
        if llm is None:
            raise ValueError(f"未定义的 LLM provider: {provider}")
        try:
            return await llm.provider.get_image(
                message=message,
                model=model,
            )
        except (APIError, httpx.RequestError, TimeoutError, ValueError) as exc:
            raise LLMRequestError(provider_error_message(exc)) from None
