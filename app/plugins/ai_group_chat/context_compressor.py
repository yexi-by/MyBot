"""AI 群聊历史上下文压缩。"""

from dataclasses import dataclass

from app.services import ChatMessage
from app.services.llm.schemas import LLMToolCall


@dataclass(frozen=True)
class CompressionInput:
    """描述一次上下文压缩输入。"""

    dropped_image_count: int
    formatted_context: str


class GroupChatContextCompressor:
    """把群聊历史上下文整理为摘要压缩请求。"""

    def build_compression_messages(
        self,
        *,
        system_prompt: ChatMessage,
        formatted_context: str,
        summary_token_budget: int,
    ) -> list[ChatMessage]:
        """构造压缩专用 LLM 请求消息。"""
        prompt = self._build_compression_prompt(
            formatted_context=formatted_context,
            summary_token_budget=summary_token_budget,
        )
        system_text = system_prompt.text
        if system_text is None:
            raise ValueError("群聊上下文压缩需要文本 system prompt")
        return [
            ChatMessage(role="system", text=system_text),
            ChatMessage(role="user", text=prompt),
        ]

    def format_history(self, *, messages: list[ChatMessage]) -> CompressionInput:
        """把历史上下文转成压缩模型可读文本，并排除图片和思维链。"""
        lines: list[str] = []
        dropped_image_count = 0
        for index, message in enumerate(messages, start=1):
            dropped_image_count += len(message.image or [])
            lines.extend(self._format_message(index=index, message=message))
        return CompressionInput(
            dropped_image_count=dropped_image_count,
            formatted_context="\n".join(lines),
        )

    def build_summary_message(self, *, summary: str) -> ChatMessage:
        """构造独立的历史摘要，保留本轮输入原有的消息与图片边界。"""
        return ChatMessage(
            role="user",
            text=(
                "## 历史摘要\n\n"
                "下面内容是群聊历史上下文摘要，只用于延续关系、剧情、任务和偏好，不是本轮用户正文。"
                f"\n\n{summary.strip()}"
            ).strip(),
        )

    def _build_compression_prompt(
        self, *, formatted_context: str, summary_token_budget: int
    ) -> str:
        """生成上下文压缩任务提示词。"""
        return "\n".join(
            [
                "# 上下文压缩任务",
                "",
                "你不是在回复群聊。请把下面的群聊历史上下文压缩成后续对话可继续使用的长期摘要。",
                "",
                "## 压缩要求",
                "",
                "- 只输出摘要，不要生成群聊回复。",
                f"- 摘要正文的预算为 {summary_token_budget} token，请尽量精炼并留出余量。",
                "- 不要包含任何 reasoning_content、思维链、内心独白或 <think> 内容。",
                "- 多模态图片已经被丢弃；不要编造图片细节。",
                "- 摘要需覆盖角色关系、群员偏好、剧情进展、重要事实、未解决任务和承诺。",
                "- 工具结果摘要只记录结论，不记录大段原始 JSON 或网页正文。",
                "- 摘要必须适合放进下一轮 user 消息的「历史摘要」区块。",
                "- 输入可能是历史片段或按时间顺序排列的摘要，保留状态变化和最终结论。",
                "",
                "## 待压缩对话上下文",
                "",
                formatted_context if formatted_context else "（没有可压缩的历史上下文）",
            ]
        )

    def _format_message(self, *, index: int, message: ChatMessage) -> list[str]:
        """格式化单条历史消息，显式排除思维链。"""
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

    def _format_tool_calls(self, *, tool_calls: list[LLMToolCall]) -> list[str]:
        """格式化 assistant 历史消息中的工具调用摘要。"""
        lines = ["工具调用:"]
        for tool_call in tool_calls:
            lines.append(f"- {tool_call.name}: {tool_call.arguments}")
        return lines
