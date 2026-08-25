"""AI 群聊插件常量。"""

from datetime import timedelta, timezone
from typing import Final

BEIJING_TIMEZONE: Final[timezone] = timezone(timedelta(hours=8))
ROLE_LABELS: Final[dict[str, str]] = {
    "owner": "群主",
    "admin": "管理员",
    "member": "群员",
}
