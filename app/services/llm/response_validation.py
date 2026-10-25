"""在模型输出产生副作用前检查响应完整性和正文中的协议残片。"""

import re
from collections.abc import Sequence
from typing import Final

from .errors import LLMResponseError
from .schemas import ChatMessage, LLMResponse


# 只匹配协议外壳，不根据「思考」「工具」等普通词语判断正文。
CONTROL_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"</?\s*(?:think|thinking|thought|analysis|reasoning|tool_call|tool_calls|"
    r"function_call|function_calls)(?=[\s>/]|$)|</?\s*call\s*:",
    re.IGNORECASE,
)
TEXT_TOOL_CALL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bcall\s*:\s*default_api\s*:\s*[\w.-]+|"
    r"\bcall\s*:\s*(?:qq__|mcp__)[\w.-]+\s*\{",
    re.IGNORECASE,
)
LITERAL_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!`)`</?\s*(?:think|thinking|thought|analysis|reasoning|tool_call|tool_calls|"
    r"function_call|function_calls)\s*>`(?!`)",
    re.IGNORECASE,
)
THOUGHT_HEADER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:🫧|💭)\ufe0f?\s*(?:thought|thinking|analysis|reasoning)(?=[\s:：`]|$)",
    re.IGNORECASE,
)
# 气泡独占一行且后面还有正文时也是思考标题；句中表情和单独的表情消息不拦截。
BARE_THOUGHT_HEADER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*(?:`(?:🫧|💭)\ufe0f?`|(?:🫧|💭)\ufe0f?)[ \t]*\r?\n(?=\s*\S)",
    re.MULTILINE,
)
LITERAL_THOUGHT_HEADER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!`)`(?:🫧|💭)\ufe0f?[ \t]*(?:thought|thinking|analysis|reasoning)`(?!`)",
    re.IGNORECASE,
)
CONTROL_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"<\|(?:channel|analysis|begin_of_thought|end_of_thought)\|>",
    re.IGNORECASE,
)
QUARANTINED_ASSISTANT_TEXT: Final[str] = "（这条机器人回复包含异常内部内容，已从模型上下文隔离）"


def validate_response(response: LLMResponse, *, tools_allowed: bool = True) -> None:
    """整份响应通过检查后才允许发送、执行工具或保存上下文。"""
    finish_reason = (response.finish_reason or "").lower()
    if finish_reason not in {"", "stop", "tool_calls"}:
        raise LLMResponseError(
            "invalid_finish_reason",
            "模型响应被截断或以异常状态结束，不能使用其中的正文和工具调用。",
        )
    if finish_reason == "tool_calls" and not response.tool_calls:
        raise LLMResponseError(
            "missing_tool_calls", "模型声明了工具调用，但没有返回完整的结构化调用。"
        )
    if response.tool_calls and not tools_allowed:
        raise LLMResponseError(
            "unexpected_tool_calls", "本次请求没有提供可调用工具，请直接返回正文。"
        )

    seen_ids: set[str] = set()
    for tool_call in response.tool_calls:
        if not tool_call.id.strip() or not tool_call.name.strip():
            raise LLMResponseError(
                "invalid_tool_call", "工具调用缺少有效的调用 ID 或工具名称。"
            )
        if tool_call.id in seen_ids:
            raise LLMResponseError(
                "duplicate_tool_call_id", "同一响应中的工具调用 ID 重复，无法关联结果。"
            )
        seen_ids.add(tool_call.id)

    validate_response_content(response.content)


def validate_response_content(content: str | None) -> None:
    """阻止内部协议片段进入正文；单独引用一个标签的行内示例可正常展示。"""
    if has_response_control_content(content):
        raise LLMResponseError(
            "control_content",
            "回复正文混入了工具调用协议或内部思考标记。",
        )


def has_response_control_content(content: str | None) -> bool:
    """只识别明确的内部标记，普通英文和 thought 等词语不构成异常。"""
    if content is None:
        return False
    # 仅豁免唯一一个行内标签示例。成对反引号标签仍可能包着实际思考，
    # 不能逐个删除后放行；代码围栏和完整协议块也不享受豁免。
    literal_tags = [
        *LITERAL_TAG_PATTERN.finditer(content),
        *LITERAL_THOUGHT_HEADER_PATTERN.finditer(content),
    ]
    text = content
    if len(literal_tags) == 1:
        literal = literal_tags[0]
        before = content[:literal.start()].rsplit("\n", 1)[-1].strip()
        after = content[literal.end():].split("\n", 1)[0].strip()
        # 独占一行的反引号标记仍可能是思考段标题，不能把后面的分析一起放行。
        if before or after or content.strip() == literal.group():
            text = content[:literal.start()] + content[literal.end():]
    return any(pattern.search(text) is not None for pattern in (
        CONTROL_TAG_PATTERN, TEXT_TOOL_CALL_PATTERN,
        THOUGHT_HEADER_PATTERN, BARE_THOUGHT_HEADER_PATTERN, CONTROL_TOKEN_PATTERN,
    ))


def isolate_assistant_history(
    messages: Sequence[ChatMessage],
) -> tuple[list[ChatMessage], int]:
    """隔离历史助手的异常正文，不改原消息；已发生的工具调用及结果仍能配对。"""
    safe_messages: list[ChatMessage] = []
    quarantined = 0
    for message in messages:
        if message.role != "assistant" or not has_response_control_content(message.text):
            safe_messages.append(message)
            continue
        quarantined += 1
        if message.tool_calls:
            # 工具已经在历史轮次执行过，保留调用与协议状态，避免留下孤立 tool 消息。
            safe_messages.append(message.model_copy(update={
                "text": None,
                "response_items": [],
            }))
    return safe_messages, quarantined
