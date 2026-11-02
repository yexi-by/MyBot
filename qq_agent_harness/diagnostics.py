"""只输出诊断元信息，不转储聊天、工具正文或推理内容。"""

import json
import logging
import re
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .contracts import JsonObject


def remote_error_metadata(data: JsonObject) -> str:
    """仅提取协议错误类别与已知请求字段，不回显服务端正文或参数值。"""
    error = data.get("error")
    if not isinstance(error, dict):
        return ""
    codes = {
        "invalid_request_error",
        "invalid_request",
        "invalid_argument",
        "invalid_value",
        "authentication_error",
        "permission_error",
        "rate_limit_exceeded",
        "insufficient_quota",
        "model_not_found",
        "unsupported_parameter",
        "server_error",
        "overloaded_error",
    }
    parts = [
        value
        for key in ("code", "type")
        if isinstance(value := error.get(key), str) and value in codes
    ]
    param = error.get("param")
    if isinstance(param, str) and re.fullmatch(
        r"(?:model|messages|input|tools|reasoning|reasoning_effort|store)(?:[.\[\]A-Za-z0-9_]{0,100})",
        param,
    ):
        parts.append("字段 " + param)
    return "；".join(dict.fromkeys(parts))


# Google API 的错误状态与 ErrorInfo 原因枚举；只回显白名单内的枚举值和对应提示。
_GOOGLE_STATUS_HINTS = {
    "INVALID_ARGUMENT": "请求参数无效，请检查音色、语言代码和文本长度",
    "FAILED_PRECONDITION": "项目条件不满足，例如未启用结算或区域受限",
    "OUT_OF_RANGE": "请求超出允许范围",
    "UNAUTHENTICATED": "访问令牌无效或已过期",
    "PERMISSION_DENIED": "服务账号无权访问，请检查 Vertex AI User 角色和项目 ID",
    "NOT_FOUND": "模型名称错误或所选区域不可用",
    "RESOURCE_EXHAUSTED": "配额或请求频率超限",
    "ABORTED": "请求被中止",
    "INTERNAL": "服务内部错误",
    "UNAVAILABLE": "服务暂时不可用",
    "DEADLINE_EXCEEDED": "服务处理超时",
}
_GOOGLE_REASON_HINTS = {
    "SERVICE_DISABLED": "项目未启用 Vertex AI API",
    "BILLING_DISABLED": "项目结算不可用，赠金到期或用完后也会出现",
    "IAM_PERMISSION_DENIED": "服务账号缺少 Vertex AI User 角色",
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT": "访问令牌权限范围不足",
    "CONSUMER_INVALID": "项目 ID 无效或项目不存在",
    "USER_PROJECT_DENIED": "服务账号无权使用该项目计费",
    "RATE_LIMIT_EXCEEDED": "请求频率超限",
    "RESOURCE_EXHAUSTED": "配额已用尽",
}


def google_error_metadata(data: JsonObject) -> str:
    """提取 Google API 错误的状态和原因枚举并附带中文提示，不回显服务端 message。"""
    error = data.get("error")
    if not isinstance(error, dict):
        return ""
    status = error.get("status")
    status = status if isinstance(status, str) and status in _GOOGLE_STATUS_HINTS else ""
    reason = ""
    details = error.get("details")
    for detail in details if isinstance(details, list) else []:
        value = detail.get("reason") if isinstance(detail, dict) else None
        if isinstance(value, str) and value in _GOOGLE_REASON_HINTS:
            reason = value
            break
    hint = _GOOGLE_REASON_HINTS.get(reason) or _GOOGLE_STATUS_HINTS.get(status, "")
    label = " / ".join(part for part in (status, reason) if part)
    return f"{label}：{hint}" if label else ""


FIELDS = (
    "resource_type",
    "source_path",
    "has_url",
    "final_source",
    "failure_reason",
    "bot_id",
    "action",
    "scope_key",
    "request_kind",
    "attempt",
    "retry",
    "max_retries",
    "delay_seconds",
    "http_status",
    "error_type",
)


class DiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in FIELDS:
            if hasattr(record, name):
                data[name] = getattr(record, name)
        if record.exc_info and record.exc_info[0]:
            data["error_type"] = record.exc_info[0].__name__
            data["frames"] = [
                {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(DiagnosticFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
