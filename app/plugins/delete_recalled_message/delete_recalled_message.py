"""收到撤回通知后删除 Redis 中对应的消息缓存。"""

from typing import ClassVar, Final, override

from app.models import FriendRecallNoticeEvent, GroupRecallNoticeEvent, NapCatId
from app.plugins.base import BasePlugin
from app.utils.log import log_event

CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 99


class DeleteRecalledMessagePlugin(
    BasePlugin[GroupRecallNoticeEvent | FriendRecallNoticeEvent]
):
    """同步清理已经被撤回的群聊或私聊消息缓存。"""

    name: ClassVar[str] = "自动删除撤回消息插件"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """撤回清理插件无需额外配置。"""

    @override
    async def run(
        self, msg: GroupRecallNoticeEvent | FriendRecallNoticeEvent
    ) -> bool:
        """删除 Redis Hash 和时间索引中的撤回消息。"""
        root, target_id = self._resolve_message_scope(msg=msg)
        hash_key = f"bot:{msg.self_id}:{root}:{target_id}:msg_data"
        zset_key = f"bot:{msg.self_id}:{root}:{target_id}:time_map"
        await self.context.database.del_data(
            hash_key=hash_key,
            zset_key=zset_key,
            msg_id=str(msg.message_id),
        )
        log_event(
            level="SUCCESS",
            event="recall_cache.deleted",
            category="plugin",
            message="已清理撤回消息缓存",
            root=root,
            target_id=target_id,
            message_id=msg.message_id,
        )
        return True

    def _resolve_message_scope(
        self, *, msg: GroupRecallNoticeEvent | FriendRecallNoticeEvent
    ) -> tuple[str, NapCatId]:
        """根据撤回通知类型生成 Redis 消息作用域。"""
        if isinstance(msg, GroupRecallNoticeEvent):
            return "group", msg.group_id
        return "private", msg.user_id
