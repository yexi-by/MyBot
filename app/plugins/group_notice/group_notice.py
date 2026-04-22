import base64

from app.models import (
    GroupDecreaseEvent,
    GroupRequestEvent,
    Image,
    MessageSegment,
    Text,
)
from app.utils import download_content, load_config_section

from ..base import BasePlugin
from .segments import PluginConfig

# 配置文件路径
PLUGINS_CONFIG_PATH = "plugins_config/plugins.toml"
CONFIG_SECTION = "shared_group"

GROUP_REQUEST_TEMPLATE = """✨ 新的加群申请 ✨

👤 用户: {nickname} ({user_id})
📝 验证信息: {comment}

请管理员尽快处理~"""

GROUP_DECREASE_LEAVE_TEMPLATE = """👋 成员离开提醒

👤 用户: {nickname} ({user_id})
💔 状态: 主动退群

江湖路远，有缘再见~"""

GROUP_DECREASE_KICK_TEMPLATE = """👢 成员变动提醒

👤 用户: {nickname} ({user_id})
🚫 状态: 被踢出群聊

请大家遵守群规哦~"""

GROUP_DECREASE_KICK_ME_TEMPLATE = """⚠️ Bot变动提醒

🤖 Bot: {nickname} ({user_id})
❌ 状态: 被移出群聊

我还会回来的！"""

# 插件配置
CONSUMERS_COUNT = 5
PRIORITY = 10
AVATAR_URL_TEMPLATE = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"


class GroupNotice(BasePlugin[GroupRequestEvent | GroupDecreaseEvent]):
    name = "GroupNotice"
    consumers_count = CONSUMERS_COUNT
    priority = PRIORITY

    def setup(self) -> None:
        config = load_config_section(
            file_path=PLUGINS_CONFIG_PATH,
            section_name=CONFIG_SECTION,
            model_cls=PluginConfig,
        )
        self.group_list: list[int] = [
            group_config.group_id for group_config in config.group_config
        ]

    async def run(self, msg: GroupRequestEvent | GroupDecreaseEvent) -> bool:
        if msg.group_id not in self.group_list:
            return False

        user_id = msg.user_id
        nickname = "未知用户"
        try:
            user_info = await self.context.bot.get_stranger_info(user_id=user_id)
            if user_info.data:
                nickname = user_info.data.get("nickname", "未知用户")
        except Exception:
            pass

        avatar_url = AVATAR_URL_TEMPLATE.format(user_id=user_id)

        file_image_base = None
        try:
            content = await download_content(
                url=avatar_url, client=self.context.direct_httpx
            )
            image_base64 = base64.b64encode(content).decode("utf-8")
            file_image_base = f"base64://{image_base64}"
        except Exception:
            pass

        text_content = ""

        match msg:
            case GroupRequestEvent():
                text_content = GROUP_REQUEST_TEMPLATE.format(
                    nickname=nickname, user_id=user_id, comment=msg.comment
                )
            case GroupDecreaseEvent(sub_type="leave"):
                text_content = GROUP_DECREASE_LEAVE_TEMPLATE.format(
                    nickname=nickname, user_id=user_id
                )
            case GroupDecreaseEvent(sub_type="kick"):
                text_content = GROUP_DECREASE_KICK_TEMPLATE.format(
                    nickname=nickname, user_id=user_id
                )
            case GroupDecreaseEvent(sub_type="kick_me"):
                text_content = GROUP_DECREASE_KICK_ME_TEMPLATE.format(
                    nickname=nickname, user_id=user_id
                )
            case _:
                return False

        message_list: list[MessageSegment] = [Text.new(text_content)]
        if file_image_base:
            message_list.append(Image.new(file=file_image_base))

        await self.context.bot.send_msg(
            group_id=msg.group_id, message_segment=message_list
        )

        return True
