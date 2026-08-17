"""Root 用户被禁言时自动解除禁言。"""

from dataclasses import dataclass
from typing import ClassVar, Final, override

from app.config import AutoUnbanConfig
from app.models import GroupBanEvent, NapCatId
from app.plugins.base import BasePlugin
from app.utils.log import log_event, log_exception

CONSUMERS_COUNT: Final[int] = 1
PRIORITY: Final[int] = 10


@dataclass(frozen=True, slots=True)
class _AutoUnbanRuntime:
    """单次配置版本对应的运行对象。"""

    config: AutoUnbanConfig
    protected_users: frozenset[NapCatId]


class AutoUnbanPlugin(BasePlugin[GroupBanEvent]):
    """检测配置用户被禁言的事件并立即调用 NapCat 解禁。"""

    name: ClassVar[str] = "自动解禁插件"
    plugin_id: ClassVar[str] = "auto_unban"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """初始化延迟构造的配置运行对象。"""
        self._runtime_revision = 0
        self._runtime: _AutoUnbanRuntime | None = None

    def _current_runtime(self) -> _AutoUnbanRuntime | None:
        """为当前插件配置版本构造一次运行对象。"""
        revision = self.plugin_config.revision
        if self._runtime_revision == revision:
            return self._runtime
        config = self.plugin_config.get(AutoUnbanConfig)
        runtime = (
            None
            if config is None
            else _AutoUnbanRuntime(
                config=config,
                protected_users=frozenset(config.protected_users),
            )
        )
        self._runtime = runtime
        self._runtime_revision = revision
        return runtime

    @override
    async def run(self, msg: GroupBanEvent) -> bool:
        """在 Root 用户被禁言时自动解除禁言。"""
        runtime = self._current_runtime()
        if runtime is None:
            return False
        if msg.sub_type != "ban":
            return False
        if msg.user_id not in runtime.protected_users:
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
