from app.models import FriendRecallNoticeEvent, GroupRecallNoticeEvent

from ..base import BasePlugin


class DeleteDatabaseMessage(
    BasePlugin[GroupRecallNoticeEvent | FriendRecallNoticeEvent]
):
    name = "删除对应撤回消息插件"
    consumers_count = 5
    priority = 1

    def setup(self) -> None: ...

    async def run(self, msg: GroupRecallNoticeEvent | FriendRecallNoticeEvent) -> bool:
        message_id = msg.message_id
        match msg:
            case GroupRecallNoticeEvent():
                root = "group"
                id_val = msg.group_id
            case FriendRecallNoticeEvent():
                root = "private"
                id_val = msg.user_id
        hash_key = f"bot:{msg.self_id}:{root}:{id_val}:msg_data"
        zset_key = f"bot:{msg.self_id}:{root}:{id_val}:time_map"
        await self.context.database.del_data(
            hash_key=hash_key, zset_key=zset_key, msg_id=str(message_id)
        )
        return True
