"""AI 群聊长期上下文 token 预算估算。"""

from dataclasses import dataclass
from math import ceil

from app.services.llm.schemas import ChatMessage


@dataclass(frozen=True)
class TokenBudgetEstimate:
    """描述一次上下文预算估算结果。"""

    estimated_tokens: int
    max_context_tokens: int
    should_compress: bool


class ConservativeTokenEstimator:
    """使用宁多不少的规则估算不同模型的上下文 token 数。"""

    def __init__(
        self,
        *,
        safety_factor: float,
        request_overhead_tokens: int,
        message_overhead_tokens: int,
        tool_call_overhead_tokens: int,
        image_tokens: int,
        ascii_tokens_per_character: float,
        non_ascii_tokens_per_character: float,
    ) -> None:
        """保存用户为当前模型配置的估算参数。"""
        self.safety_factor: float = safety_factor
        self.request_overhead_tokens: int = request_overhead_tokens
        self.message_overhead_tokens: int = message_overhead_tokens
        self.tool_call_overhead_tokens: int = tool_call_overhead_tokens
        self.image_tokens: int = image_tokens
        self.ascii_tokens_per_character: float = ascii_tokens_per_character
        self.non_ascii_tokens_per_character: float = non_ascii_tokens_per_character

    def estimate_messages(self, *, messages: list[ChatMessage]) -> int:
        """估算消息及固定封装的 token 数，不计临时工具定义。"""
        raw_tokens = self.request_overhead_tokens
        for message in messages:
            raw_tokens += self._estimate_message(message=message)
        return ceil(raw_tokens * self.safety_factor)

    def check_context(
        self,
        *,
        messages: list[ChatMessage],
        max_context_tokens: int,
    ) -> TokenBudgetEstimate:
        """判断长期上下文是否需要压缩。"""
        estimated_tokens = self.estimate_messages(messages=messages)
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
        tokens = 0.0
        for character in text:
            if character.isascii():
                tokens += self.ascii_tokens_per_character
                continue
            tokens += self.non_ascii_tokens_per_character
        return ceil(tokens)
