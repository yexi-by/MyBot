"""PostgreSQL 消息仓库的公共值对象。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models import MessageSegment

type MessageDirection = Literal["incoming", "outgoing"]
type ImageArchiveStatus = Literal["pending", "leased", "stored", "retry", "failed"]


@dataclass(frozen=True, slots=True)
class GroupDataScope:
    """唯一确定一个机器人所在群的数据范围。"""

    bot_id: str
    group_id: str

    def __post_init__(self) -> None:
        """拒绝可能导致跨机器人或跨群读写的空标识。"""
        if self.bot_id.strip() == "":
            raise ValueError("bot_id 不能为空")
        if self.group_id.strip() == "":
            raise ValueError("group_id 不能为空")


@dataclass(frozen=True, slots=True)
class MessageCursor:
    """使用消息时间和数据库行 ID 稳定分页。"""

    occurred_at: datetime
    row_id: int

    def __post_init__(self) -> None:
        """分页游标必须使用带时区时间和有效行 ID。"""
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at 必须带时区")
        if self.row_id < 1:
            raise ValueError("row_id 必须大于等于 1")


@dataclass(frozen=True, slots=True)
class StoredGroupImage:
    """群消息中一张图片的持久化状态。"""

    row_id: int
    segment_index: int
    source_file: str | None
    source_url: str | None
    file_id: str | None
    status: ImageArchiveStatus
    storage_key: str | None
    mime_type: str | None
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class StoredGroupMessage:
    """供核心功能和插件读取的群消息。"""

    row_id: int
    scope: GroupDataScope
    message_id: str
    group_name: str | None
    sender_id: str
    sender_name: str
    sender_role: str | None
    occurred_at: datetime
    direction: MessageDirection
    segments: tuple[MessageSegment, ...]
    images: tuple[StoredGroupImage, ...]

    @property
    def cursor(self) -> MessageCursor:
        """生成继续查询更旧消息所需的游标。"""
        return MessageCursor(occurred_at=self.occurred_at, row_id=self.row_id)
