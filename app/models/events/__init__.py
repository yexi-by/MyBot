"""NapCat 事件模型导出。"""

from typing import Annotated

from pydantic import Field

from .message_event import BaseMessage, GroupMessage, MessageEvent, PrivateMessage, Sender
from .meta_event import HeartBeat, LifeCycle, Meta, MetaEvent
from .notice_event import (
    BotOfflineEvent,
    FriendAddNoticeEvent,
    FriendPokeEvent,
    FriendRecallNoticeEvent,
    GroupAdminNoticeEvent,
    GroupBanEvent,
    GroupCardEvent,
    GroupDecreaseEvent,
    GroupEssenceEvent,
    GroupHonorEvent,
    GroupIncreaseEvent,
    GroupLuckyKingEvent,
    GroupMsgEmojiLikeEvent,
    GroupNameEvent,
    GroupNoticeEvent,
    GroupPokeEvent,
    GroupRecallNoticeEvent,
    GroupTitleEvent,
    GroupUploadNoticeEvent,
    InputStatusEvent,
    Notice,
    NoticeEvent,
    NotifyEvent,
    PokeEvent,
    ProfileLikeEvent,
)
from .request_event import FriendRequestEvent, GroupRequestEvent, Request, RequestEvent
from .response import Response

type BotEvent = Annotated[
    MessageEvent | MetaEvent | NoticeEvent | RequestEvent,
    Field(discriminator="post_type"),
]

type AllEvent = BotEvent | Response

__all__ = [
    "AllEvent",
    "BaseMessage",
    "BotEvent",
    "BotOfflineEvent",
    "FriendAddNoticeEvent",
    "FriendPokeEvent",
    "FriendRecallNoticeEvent",
    "FriendRequestEvent",
    "GroupAdminNoticeEvent",
    "GroupBanEvent",
    "GroupCardEvent",
    "GroupDecreaseEvent",
    "GroupEssenceEvent",
    "GroupHonorEvent",
    "GroupIncreaseEvent",
    "GroupLuckyKingEvent",
    "GroupMessage",
    "GroupMsgEmojiLikeEvent",
    "GroupNameEvent",
    "GroupNoticeEvent",
    "GroupPokeEvent",
    "GroupRecallNoticeEvent",
    "GroupRequestEvent",
    "GroupTitleEvent",
    "GroupUploadNoticeEvent",
    "HeartBeat",
    "InputStatusEvent",
    "LifeCycle",
    "MessageEvent",
    "Meta",
    "MetaEvent",
    "Notice",
    "NoticeEvent",
    "NotifyEvent",
    "PokeEvent",
    "PrivateMessage",
    "ProfileLikeEvent",
    "Request",
    "RequestEvent",
    "Response",
    "Sender",
]
