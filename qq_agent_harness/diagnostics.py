"""只输出诊断元信息，不转储聊天、工具正文或推理内容。"""

import json
import logging
from datetime import UTC, datetime

FIELDS = (
    "resource_type",
    "source_path",
    "has_url",
    "final_source",
    "failure_reason",
    "bot_id",
    "action",
    "scope_key",
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
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(DiagnosticFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
