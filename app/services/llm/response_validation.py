"""在模型输出产生副作用前检查响应完整性和正文中的协议残片。"""

import re
from typing import Final

from .errors import LLMResponseError
from .schemas import ChatMessage, LLMResponse


# 只匹配协议外壳，不根据「思考」「工具」等普通词语判断正文。
CONTROL_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"</?\s*(?:think|thought|analysis|reasoning|tool_call|tool_calls|"
    r"function_call|function_calls)(?=[\s>/]|$)|</?\s*call\s*:",
    re.IGNORECASE,
)
TEXT_TOOL_CALL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bcall\s*:\s*default_api\s*:\s*[\w.-]+|"
    r"\bcall\s*:\s*(?:qq__|mcp__)[\w.-]+\s*\{",
    re.IGNORECASE,
)
LITERAL_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!`)`</?\s*(?:think|thought|analysis|reasoning|tool_call|tool_calls|"
    r"function_call|function_calls)\s*>`(?!`)",
    re.IGNORECASE,
)
NATIVE_FAILURE_REASONS: Final[frozenset[str]] = frozenset({
    "length", "max_tokens", "malformed_function_call", "unexpected_tool_call",
    "content_filter", "safety", "recitation", "blocklist", "prohibited_content",
    "spii", "image_safety", "image_prohibited_content", "image_other", "no_image",
    "model_armor", "refusal", "error", "other",
})


def validate_response(response: LLMResponse, *, tools_allowed: bool = True) -> None:
    """整份响应通过检查后才允许发送、执行工具或保存上下文。"""
    finish_reason = (response.finish_reason or "").lower()
    native_finish_reason = (response.native_finish_reason or "").lower()
    if finish_reason not in {"", "stop", "tool_calls"} or (
        native_finish_reason in NATIVE_FAILURE_REASONS
    ):
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
    if content is None:
        return
    # 仅豁免唯一一个行内标签示例。成对反引号标签仍可能包着实际思考，
    # 不能逐个删除后放行；代码围栏和完整协议块也不享受豁免。
    literal_tags = list(LITERAL_TAG_PATTERN.finditer(content))
    text = LITERAL_TAG_PATTERN.sub("", content) if len(literal_tags) == 1 else content
    if CONTROL_TAG_PATTERN.search(text) or TEXT_TOOL_CALL_PATTERN.search(text):
        raise LLMResponseError(
            "control_content",
            "回复正文混入了工具调用协议或内部思考标记。",
        )


def build_response_repair_message(error: LLMResponseError) -> ChatMessage:
    """生成仅用于本次重试的纠错说明，不回放被拒绝的模型内容。"""
    return ChatMessage(
        role="user",
        text=(
            "上一条模型响应未通过格式检查，整条响应已丢弃，"
            "其中的文字没有交付，工具也没有执行。"
            f"原因：{error}"
            "请重新生成完整响应：需要使用已提供的工具时，通过原生 tool_calls 字段"
            "提交调用，并使用合法 JSON 对象参数和唯一调用 ID；"
            "回复正文只包含可直接交付的内容，不要混入内部思考或工具调用协议。"
            "若服务限制了输出，请遵守该限制并返回简短说明。"
        ),
    )
