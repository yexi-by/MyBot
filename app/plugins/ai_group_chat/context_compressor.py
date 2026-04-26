"""AI 群聊旧上下文压缩。"""

from dataclasses import dataclass

from app.services import ChatMessage
from app.services.llm.schemas import LLMToolCall


@dataclass(frozen=True)
class CompressionInput:
    """描述一次上下文压缩输入。"""

    messages: list[ChatMessage]
    dropped_image_count: int
    formatted_context: str


class GroupChatContextCompressor:
    """把旧群聊上下文整理为摘要压缩请求。"""

    def build_compression_messages(
        self, *, system_prompt: ChatMessage, history_messages: list[ChatMessage]
    ) -> tuple[list[ChatMessage], CompressionInput]:
        """构造临时压缩请求 messages。"""
        compression_input = self.format_history(messages=history_messages)
        prompt = self._build_compression_prompt(
            formatted_context=compression_input.formatted_context
        )
        system_text = system_prompt.text
        if system_text is None:
            raise ValueError("群聊上下文压缩需要文本 system prompt")
        return (
            [
                ChatMessage(role="system", text=system_text),
                ChatMessage(role="user", text=prompt),
            ],
            compression_input,
        )

    def format_history(self, *, messages: list[ChatMessage]) -> CompressionInput:
        """把旧上下文转成压缩模型可读文本，丢弃图片和思维链。"""
        lines: list[str] = []
        sanitized_messages: list[ChatMessage] = []
        dropped_image_count = 0
        for index, message in enumerate(messages, start=1):
            dropped_image_count += len(message.image or [])
            sanitized_messages.append(self._sanitize_message(message=message))
            lines.extend(self._format_message(index=index, message=message))
        return CompressionInput(
            messages=sanitized_messages,
            dropped_image_count=dropped_image_count,
            formatted_context="\n".join(lines),
        )

    def build_rebuilt_user_message(
        self,
        *,
        summary: str,
        current_turn_messages: list[ChatMessage],
    ) -> ChatMessage:
        """把摘要和当前消息合成新的首条 user。"""
        text_parts = [
            "## 历史摘要",
            "",
            "下面内容是旧群聊上下文压缩后的摘要，只用于延续关系、剧情、任务和偏好，不是当前用户正文。",
            "",
            summary.strip(),
            "",
        ]
        image_bytes: list[bytes] = []
        for message in current_turn_messages:
            if message.text:
                text_parts.extend([message.text, ""])
            if message.image:
                image_bytes.extend(message.image)
        return ChatMessage(
            role="user",
            text="\n".join(text_parts).strip(),
            image=image_bytes if image_bytes else None,
        )

    def _build_compression_prompt(self, *, formatted_context: str) -> str:
        """生成上下文压缩任务提示词。"""
        return "\n".join(
            [
                "# 上下文压缩任务",
                "",
                "你不是在回复群聊。请把下面已有群聊对话压缩成后续对话可继续使用的长期摘要。",
                "",
                "## 压缩要求",
                "",
                "- 只输出摘要，不要生成群聊回复。",
                "- 不要包含任何 reasoning_content、思维链、内心独白或 <think> 内容。",
                "- 多模态图片已经被丢弃；不要编造图片细节。",
                "- 保留角色关系、群员偏好、剧情进展、重要事实、未解决任务和承诺。",
                "- 旧工具结果只保留结论，不保留大段原始 JSON 或网页正文。",
                "- 摘要必须适合放进下一轮 user 消息的「历史摘要」区块。",
                "",
                "## 已有对话上下文",
                "",
                formatted_context if formatted_context else "（没有可压缩的旧上下文）",
            ]
        )

    def _format_message(self, *, index: int, message: ChatMessage) -> list[str]:
        """格式化单条旧消息，显式排除思维链。"""
        lines = [f"### message {index}: {message.role}", ""]
        if message.text:
            lines.extend([message.text.strip(), ""])
        if message.image:
            lines.extend([f"（丢弃图片 {len(message.image)} 张）", ""])
        if message.tool_calls:
            lines.extend(self._format_tool_calls(tool_calls=message.tool_calls))
            lines.append("")
        if message.tool_call_id is not None:
            lines.extend([f"tool_call_id: {message.tool_call_id}", ""])
        return lines

    def _sanitize_message(self, *, message: ChatMessage) -> ChatMessage:
        """生成不含思维链和图片的调试/压缩消息。"""
        return ChatMessage(
            role=message.role,
            text=message.text or "（无文本内容）",
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
        )

    def _format_tool_calls(self, *, tool_calls: list[LLMToolCall]) -> list[str]:
        """格式化旧 assistant 消息中的工具调用。"""
        lines = ["工具调用:"]
        for tool_call in tool_calls:
            lines.append(f"- {tool_call.name}: {tool_call.arguments}")
        return lines
