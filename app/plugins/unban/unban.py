from pydantic_settings import BaseSettings

from app.models import GroupBanEvent
from app.utils import load_config_section, logger

from ..base import BasePlugin

PLUGINS_CONFIG_PATH = "plugins_config/plugins.toml"
CONFIG_SECTION = "unban"


class PluginConfig(BaseSettings):
    root_id: list[int]


class UnbanPlugin(BasePlugin[GroupBanEvent]):
    """自动解禁插件 - 当配置的Root用户被禁言时自动解禁"""

    name = "自动解禁插件"
    consumers_count = 1
    priority = 10

    def setup(self) -> None:
        self.config = load_config_section(
            file_path=PLUGINS_CONFIG_PATH,
            section_name=CONFIG_SECTION,
            model_cls=PluginConfig,
        )

    async def run(self, msg: GroupBanEvent) -> bool:
        # 检查是否是禁言操作 (sub_type: ban)
        if msg.sub_type != "ban":
            return False

        # 检查被禁言的用户是否在 root_id 列表中
        if msg.user_id not in self.config.root_id:
            return False

        logger.info(
            f"检测到 Root 用户 {msg.user_id} 在群 {msg.group_id} 被禁言，正在尝试解禁..."
        )

        try:
            # 解除禁言 (duration=0)
            await self.context.bot.set_group_ban(
                group_id=msg.group_id, user_id=msg.user_id, duration=0
            )
            logger.info(f"Root 用户 {msg.user_id} 在群 {msg.group_id} 解禁成功")
            return True
        except Exception as e:
            logger.error(f"Root 用户 {msg.user_id} 在群 {msg.group_id} 解禁失败: {e}")
            return False
