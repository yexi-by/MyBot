"""Root 用户被禁言时自动解除禁言。"""

from typing import ClassVar, Final, override

from app.config.plugin_config import load_plugin_config
from app.models import GroupBanEvent, NapCatId, StrictModel
from app.plugins.base import BasePlugin
from app.utils.log import log_event, log_exception

CONFIG_SECTION: Final[str] = "auto_unban"
CONSUMERS_COUNT: Final[int] = 1
PRIORITY: Final[int] = 10


class AutoUnbanConfig(StrictModel):
    """自动解禁插件配置。"""

    root_ids: list[NapCatId]


class AutoUnbanPlugin(BasePlugin[GroupBanEvent]):
    """检测配置用户被禁言的事件并立即调用 NapCat 解禁。"""

    name: ClassVar[str] = "自动解禁插件"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """读取需要保护的 Root 用户列表。"""
        self.config: AutoUnbanConfig = load_plugin_config(
            section_name=CONFIG_SECTION,
            model_cls=AutoUnbanConfig,
        )
        self.root_ids: set[NapCatId] = set(self.config.root_ids)

    @override
    async def run(self, msg: GroupBanEvent) -> bool:
        """在 Root 用户被禁言时自动解除禁言。"""
        if msg.sub_type != "ban":
            return False
        if msg.user_id not in self.root_ids:
            return False
        log_event(
            level="INFO",
            event="auto_unban.detected",
            category="plugin",
            message="检测到 Root 用户被禁言，准备解禁",
            group_id=msg.group_id,
            user_id=msg.user_id,
        )
        try:
            await self.context.bot.set_group_ban(
                group_id=msg.group_id,
                user_id=msg.user_id,
                duration=0,
            )
        except Exception as exc:
            log_exception(
                event="auto_unban.failed",
                category="plugin",
                message="Root 用户自动解禁失败",
                exc=exc,
                group_id=msg.group_id,
                user_id=msg.user_id,
            )
            raise
        log_event(
            level="SUCCESS",
            event="auto_unban.done",
            category="plugin",
            message="Root 用户自动解禁成功",
            group_id=msg.group_id,
            user_id=msg.user_id,
        )
        return True
