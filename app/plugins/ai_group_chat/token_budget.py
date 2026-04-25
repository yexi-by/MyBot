"""AI 群聊上下文 token 预算估算。"""

from dataclasses import dataclass
from math import ceil

from app.models import to_json_value
from app.services.llm.schemas import ChatMessage, LLMToolDefinition


@dataclass(frozen=True)
class TokenBudgetEstimate:
    """描述一次上下文预算估算结果。"""

    estimated_tokens: int
    max_context_tokens: int
    should_compress: bool


class ConservativeTokenEstimator:
    """使用宁多不少的规则估算不同模型的上下文 token 数。"""

    def __init__(self, *, safety_factor: float) -> None:
        """保存最终安全系数。"""
        self.safety_factor: float = safety_factor
        self.request_overhead_tokens: int = 128
        self.message_overhead_tokens: int = 16
        self.tool_call_overhead_tokens: int = 64
        self.image_tokens: int = 1024

    def estimate_request(
        self, *, messages: list[ChatMessage], tools: list[LLMToolDefinition]
    ) -> int:
        """估算一次 LLM 请求的 token 数。"""
        raw_tokens = self.request_overhead_tokens
        for message in messages:
            raw_tokens += self._estimate_message(message=message)
        for tool in tools:
            raw_tokens += self.tool_call_overhead_tokens
            raw_tokens += self._estimate_text(text=str(to_json_value(tool)))
        return ceil(raw_tokens * self.safety_factor)

    def check_request(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[LLMToolDefinition],
        max_context_tokens: int,
    ) -> TokenBudgetEstimate:
        """判断一次 LLM 请求是否超过指定上下文预算。"""
        estimated_tokens = self.estimate_request(messages=messages, tools=tools)
        return TokenBudgetEstimate(
            estimated_tokens=estimated_tokens,
            max_context_tokens=max_context_tokens,
            should_compress=estimated_tokens > max_context_tokens,
        )

    def _estimate_message(self, *, message: ChatMessage) -> int:
        """估算单条消息的 token 数。"""
        tokens = self.message_overhead_tokens
        tokens += self._estimate_text(text=message.role)
        tokens += self._estimate_text(text=message.text)
        tokens += self._estimate_text(text=message.reasoning_content)
        tokens += self.image_tokens * len(message.image or [])
        tokens += self._estimate_text(text=message.tool_call_id)
        for tool_call in message.tool_calls or []:
            tokens += self.tool_call_overhead_tokens
            tokens += self._estimate_text(text=tool_call.id)
            tokens += self._estimate_text(text=tool_call.name)
            tokens += self._estimate_text(text=str(tool_call.arguments))
        return tokens

    def _estimate_text(self, *, text: str | None) -> int:
        """按字符保守估算文本 token，非 ASCII 字符按两个 token 计算。"""
        if text is None:
            return 0
        tokens = 0
        for character in text:
            if character.isascii():
                tokens += 1
                continue
            tokens += 2
        return tokens
