"""AI 群聊插件常量。"""

from datetime import timedelta, timezone
from typing import Final

BEIJING_TIMEZONE: Final[timezone] = timezone(timedelta(hours=8))
ROLE_LABELS: Final[dict[str, str]] = {
    "owner": "群主",
    "admin": "管理员",
    "member": "群员",
}
# 聊天记录只标出群主和管理员，普通群员的身份对语境没有帮助。
NOTABLE_ROLES: Final[frozenset[str]] = frozenset(("owner", "admin"))
WEEKDAY_LABELS: Final[tuple[str, ...]] = (
    "周一", "周二", "周三", "周四", "周五", "周六", "周日",
)
