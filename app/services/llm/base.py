"""LLM 服务商基类。"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import (
        ChatMessage,
        LLMResponse,
        LLMToolChoice,
        LLMToolDefinition,
    )


class LLMProvider(ABC):
    """所有 LLM 服务商必须继承的基类。"""

    @abstractmethod
    async def get_ai_response(
        self,
        messages: list["ChatMessage"],
        model: str,
    ) -> str:
        """获取文本响应。"""
        raise NotImplementedError

    async def get_ai_response_with_tools(
        self,
        messages: list["ChatMessage"],
        model: str,
        tools: list["LLMToolDefinition"],
        tool_choice: "LLMToolChoice" = "auto",
        parallel_tool_calls: bool = True,
    ) -> "LLMResponse":
        """获取可能包含工具调用的结构化响应。"""
        _ = (messages, model, tools, tool_choice, parallel_tool_calls)
        raise NotImplementedError(f"{self.__class__.__name__} 不支持工具调用")

    async def get_image(
        self,
        message: "ChatMessage",
        model: str,
    ) -> str:
        """生成图片，不支持该能力的服务商会显式报错。"""
        _ = (message, model)
        raise NotImplementedError(f"{self.__class__.__name__} 不支持图像生成功能")
