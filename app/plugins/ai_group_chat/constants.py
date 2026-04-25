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
MESSAGE_MODIFIER_BEHAVIOR_PROMPT: Final[str] = (
    "## 群聊消息动作偏好\n"
    "当用户明确要求你回复某条消息、艾特某人、提醒某人，或上下文中使用引用和艾特能让回复更清楚时，"
    "你应更主动地使用可用的 QQ 消息修饰动作来完成表达。"
    "消息修饰动作只负责最终消息的形式，仍然需要给出自然、完整、可以直接发送的正文。"
)
DEEPSEEK_V4_ROLEPLAY_MODELS: Final[frozenset[str]] = frozenset(
    {"deepseek-v4-pro", "deepseek-v4-flash"}
)
DEEPSEEK_V4_ROLEPLAY_INSTRUCT: Final[str] = (
    "【角色沉浸要求】在你的思考过程（<think>标签内）中，请遵守以下规则：\n"
    "1. 请以角色第一人称进行内心独白，用括号包裹内心活动，例如"
    "\"（心想：……）\"或\"(内心OS：……)\"\n"
    "2. 用第一人称描写角色的内心感受，例如\"我心想\"\"我觉得\"\"我暗自\"等\n"
    "3. 思考内容应沉浸在角色中，通过内心独白分析剧情和规划回复"
)
