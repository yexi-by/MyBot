"""群消息持久化的窄接口。"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.models import GroupMessage, MessageSegment

from .schemas import GroupDataScope, MessageCursor, StoredGroupMessage


class GroupMessageReader(Protocol):
    """只读取未撤回群消息的公共接口。"""

    async def get_active(
        self, *, scope: GroupDataScope, message_id: str
    ) -> StoredGroupMessage | None:
        """按消息 ID 读取一条未撤回消息。"""
        ...

    async def list_recent(
        self,
        *,
        scope: GroupDataScope,
        limit: int,
        before: MessageCursor | None = None,
        sender_id: str | None = None,
    ) -> list[StoredGroupMessage]:
        """按从新到旧的顺序读取群消息。"""
        ...

    async def list_between(
        self,
        *,
        scope: GroupDataScope,
        start: datetime,
        end: datetime,
        limit: int,
        before: MessageCursor | None = None,
        sender_id: str | None = None,
    ) -> list[StoredGroupMessage]:
        """读取半开时间区间 [start, end) 内的消息。"""
        ...

    async def list_around(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        before_count: int,
        after_count: int,
        sender_id: str | None = None,
    ) -> list[StoredGroupMessage]:
        """按时间正序读取锚点消息的前后文。"""
        ...


class IncomingMessageWriter(Protocol):
    """持久化 NapCat 入站群消息。"""

    async def save_incoming(self, message: GroupMessage) -> None:
        """幂等写入一条入站群消息和其图片任务。"""
        ...


class SentMessageRecorder(Protocol):
    """记录已被 NapCat 确认发送的群消息。"""

    async def record_sent(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        segments: Sequence[MessageSegment],
        occurred_at: datetime | None = None,
    ) -> None:
        """写入出站群消息，不记录私聊。"""
        ...


class RecallArchiver(Protocol):
    """将已存在的群消息标记为撤回归档。"""

    async def archive(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        recalled_at: datetime,
        recalled_by_id: str,
    ) -> bool:
        """保留首次撤回证据，并返回目标消息是否存在。"""
        ...
