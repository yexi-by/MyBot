"""有界上下文预算与低权限摘要的数据处理。"""

import json
import math

from .contracts import JsonObject, JsonValue


class ProviderError(RuntimeError):
    """可直接显示的、已脱敏的模型错误。"""


class ContextLimitError(ProviderError):
    pass


class CompactionError(ProviderError):
    pass


class ModelRequestLimit(ProviderError):
    pass


class ContextWindowExceeded(ProviderError):
    pass


def estimate_tokens(value: JsonValue) -> int:
    """使用保守文本估算；媒体单独计量，不把 base64 当自然语言。"""
    if isinstance(value, dict):
        kind = value.get("type")
        if kind in ("image_url", "input_image"):
            return 4096
        if kind == "input_audio":
            audio = value.get("input_audio")
            encoded = audio.get("data") if isinstance(audio, dict) else None
            # 缺少时长时按压缩音频的保守字节预算估算，后续由 API usage 校准。
            return (
                max(4096, math.ceil(len(encoded) * 3 / 4 / 64))
                if isinstance(encoded, str)
                else 4096
            )
        return 4 + sum(estimate_tokens(key) + estimate_tokens(item) for key, item in value.items())
    if isinstance(value, list):
        return 2 + sum(estimate_tokens(item) for item in value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, math.ceil(len(text.encode("utf-8")) / 2))


def visible_data(value: JsonValue) -> JsonValue:
    """摘要只接收可见内容；推理正文、加密推理及媒体负载不能进入摘要。"""
    if isinstance(value, dict):
        kind = value.get("type")
        if kind == "reasoning":
            return None
        if kind in ("image_url", "input_image", "input_audio"):
            return {"type": kind, "media": "原生媒体内容省略"}
        return {
            key: visible_data(item)
            for key, item in value.items()
            if key not in ("reasoning_content", "reasoning_details", "encrypted_content")
        }
    if isinstance(value, list):
        return [
            visible_data(item)
            for item in value
            if not (isinstance(item, dict) and item.get("type") == "reasoning")
        ]
    return value


def usage_totals(data: JsonObject, protocol: str) -> tuple[int | None, int | None]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    input_name, output_name = (
        ("prompt_tokens", "completion_tokens")
        if protocol == "chat_completions"
        else ("input_tokens", "output_tokens")
    )

    def count(name: str) -> int | None:
        value = usage.get(name)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    # 两种协议的原始 input/prompt 总量已含缓存，output/completion 已含 reasoning。
    return count(input_name), count(output_name)


def is_context_overflow(data: JsonObject) -> bool:
    error = data.get("error")
    if not isinstance(error, dict):
        return False
    codes = {
        value[:128].lower() for key in ("code", "type") if isinstance(value := error.get(key), str)
    }
    if codes & {
        "context_length_exceeded",
        "context_window_exceeded",
        "max_context_length_exceeded",
    }:
        return True
    message_value = error.get("message")
    message = message_value[:4096].lower() if isinstance(message_value, str) else ""
    return any(
        phrase in message
        for phrase in (
            "maximum context length",
            "exceeds the context window",
            "context window exceeded",
            "input tokens exceed",
            "prompt is too long",
        )
    )
