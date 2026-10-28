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
