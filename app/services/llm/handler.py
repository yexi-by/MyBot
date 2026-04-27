"""LLM 服务注册、路由与工具调用循环。"""

from typing import Self

from openai import AsyncOpenAI

from .providers.openai import OpenAIService
from .schemas import (
    ChatMessage,
    LLMConfig,
    LLMProviderWrapper,
    LLMResponse,
    LLMToolChoice,
    LLMToolDefinition,
    LLMToolExecutor,
)
from .tools import build_tool_result_message
from .wrapper import ResilientLLMProvider


class LLMHandler:
    """按模型厂商路由到具体 LLM 服务。"""

    def __init__(self, services: list[LLMProviderWrapper]) -> None:
        """保存已注册服务列表。"""
        self.services: list[LLMProviderWrapper] = services

    @classmethod
    def register_instance(cls, settings: list[LLMConfig]) -> Self:
        """根据配置注册 LLM 服务实例。"""
        services: list[LLMProviderWrapper] = []
        for model_config in settings:
            raw_service = OpenAIService(
                client=AsyncOpenAI(
                    api_key=model_config.api_key,
                    base_url=model_config.base_url,
                )
            )
            safe_service = ResilientLLMProvider(
                inner_provider=raw_service, llm_config=model_config
            )
            wrapper = LLMProviderWrapper(
                model_vendors=model_config.model_vendors,
                provider=safe_service,
            )
            services.append(wrapper)
        return cls(services=services)

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """获取指定模型厂商的文本响应。"""
        for llm in self.services:
            if llm.model_vendors != model_vendors:
                continue
            return await llm.provider.get_ai_response(
                messages=messages, model=model_name
            )
        raise ValueError(f"未定义的服务商名:{model_vendors}")

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """获取指定模型厂商的工具调用结构化响应。"""
        for llm in self.services:
            if llm.model_vendors != model_vendors:
                continue
            return await llm.provider.get_ai_response_with_tools(
                messages=messages,
                model=model_name,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
            )
        raise ValueError(f"未定义的服务商名:{model_vendors}")

    async def run_ai_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tool_executor: LLMToolExecutor,
        max_tool_rounds: int = 8,
    ) -> str:
        """执行完整工具调用循环，直到模型返回最终文本。"""
        working_messages = messages[:]
        tools = tool_executor.list_tools()
        if not tools:
            return await self.get_ai_text_response(
                messages=working_messages,
                model_vendors=model_vendors,
                model_name=model_name,
            )
        for _ in range(max_tool_rounds):
            response = await self.get_ai_response_with_tools(
                messages=working_messages,
                model_vendors=model_vendors,
                model_name=model_name,
                tools=tools,
            )
            if not response.tool_calls:
                if response.content is None:
                    raise ValueError("LLM 工具循环返回了空文本")
                return response.content
            working_messages.append(
                ChatMessage(
                    role="assistant",
                    text=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for tool_call in response.tool_calls:
                result = await tool_executor.call_tool(
                    name=tool_call.name, arguments=tool_call.arguments
                )
                working_messages.append(
                    build_tool_result_message(
                        tool_call_id=tool_call.id,
                        result=result,
                    )
                )
        raise RuntimeError(f"LLM 工具调用超过最大轮数: {max_tool_rounds}")

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
        model_vendors: str,
    ) -> str:
        """获取指定模型厂商的图片响应。"""
        for llm in self.services:
            if llm.model_vendors != model_vendors:
                continue
            return await llm.provider.get_image(
                message=message,
                model=model,
            )
        raise ValueError(f"未定义的服务商名:{model_vendors}")
