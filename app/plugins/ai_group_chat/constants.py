"""AI 群聊插件常量。"""

from datetime import timedelta, timezone
from pathlib import Path
from typing import Final

DEBUG_DUMP_DIR: Final[Path] = Path("logs/ai_group_chat_debug")
BEIJING_TIMEZONE: Final[timezone] = timezone(timedelta(hours=8))
CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 5
ROLE_LABELS: Final[dict[str, str]] = {
    "owner": "群主",
    "admin": "管理员",
    "member": "群员",
}
