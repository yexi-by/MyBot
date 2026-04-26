"""AI 群聊插件常量。"""

from datetime import timedelta, timezone
from pathlib import Path
from typing import Final

CONFIG_PATH: Final[Path] = Path("plugins_config/plugins.toml")
CONFIG_SECTION: Final[str] = "ai_group_chat"
DEBUG_DUMP_DIR: Final[Path] = Path("plugins_config/ai_group_chat_debug")
BEIJING_TIMEZONE: Final[timezone] = timezone(timedelta(hours=8))
CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 5
ROLE_LABELS: Final[dict[str, str]] = {
    "owner": "群主",
    "admin": "管理员",
    "member": "群员",
}
DEEPSEEK_V4_ROLEPLAY_MODELS: Final[frozenset[str]] = frozenset(
    {"deepseek-v4-pro", "deepseek-v4-flash"}
)
