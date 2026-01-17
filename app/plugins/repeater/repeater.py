from app.models import GroupMessage
from ..base import BasePlugin
from pydantic_settings import BaseSettings
from pydantic import BaseModel
from app.utils import load_config

GROUP_CONFIG_PATH = "plugins_config/nanobanana_config.toml"


class GroupConfig(BaseModel):
    group_id: int


class PluginConfig(BaseSettings):
    group_config: list[GroupConfig]


# 插件配置
CONSUMERS_COUNT = 1
PRIORITY = 10


class Repeater(BasePlugin[GroupMessage]):
    """复读机插件 - 当群里出现连续两条一样的文本消息时复读"""

    name = "复读机插件"
    consumers_count = CONSUMERS_COUNT
    priority = PRIORITY

    def setup(self) -> None:
        self.config = load_config(file_path=GROUP_CONFIG_PATH, model_cls=PluginConfig)
        self.group_list = [
            group_config.group_id for group_config in self.config.group_config
        ]
        # 存储每个群的上一条消息内容和计数
        # 格式: {group_id: {"content": str, "count": int, "repeated": bool}}
        self.last_messages: dict[int, dict] = {}

    def _extract_text(self, msg: GroupMessage) -> str:
        """从消息中提取纯文本内容"""
        text_parts = []
        for segment in msg.message:
            if segment.type == "text":
                text_parts.append(segment.data.text)
        return "".join(text_parts).strip()

    async def run(self, msg: GroupMessage) -> bool:
        group_id = msg.group_id
        user_id = msg.user_id
        bot_id = msg.self_id

        # 检查是否在配置的群列表中
        if group_id not in self.group_list:
            return False

        # 忽略机器人自己发送的消息
        if user_id == bot_id:
            return False

        # 提取当前消息的文本内容
        current_text = self._extract_text(msg)

        # 如果没有文本内容，跳过
        if not current_text:
            return False

        # 获取该群的上一条消息记录
        last_record = self.last_messages.get(group_id)

        if last_record is None:
            # 第一条消息，初始化记录
            self.last_messages[group_id] = {
                "content": current_text,
                "count": 1,
                "repeated": False,
            }
            return False

        if last_record["content"] == current_text:
            # 消息内容相同
            last_record["count"] += 1

            # 当连续出现两条相同消息且还未复读过时，进行复读
            if last_record["count"] >= 2 and not last_record["repeated"]:
                last_record["repeated"] = True
                # 复读消息
                await self.context.bot.send_msg(
                    group_id=group_id,
                    text=current_text,
                )
                return True
        else:
            # 消息内容不同，重置记录
            self.last_messages[group_id] = {
                "content": current_text,
                "count": 1,
                "repeated": False,
            }

        return False
