from typing import Annotated

from pydantic import Field

from .message_event import (
    BaseMessage,
    GroupMessage,
    MessageEvent,
    PrivateMessage,
    Sender,
)
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

type AllEvent = Annotated[
    MessageEvent | MetaEvent | NoticeEvent | RequestEvent,
    Field(discriminator="post_type"),
]
__all__ = [
    # AllEvent
    "AllEvent",
    # message_event
    "MessageEvent",
    "BaseMessage",
    "Sender",
    "GroupMessage",
    "PrivateMessage",
    # meta_event
    "MetaEvent",
    "Meta",
    "LifeCycle",
    "HeartBeat",
    # notice_event
    "Notice",
    "NoticeEvent",
    "NotifyEvent",
    "PokeEvent",
    "GroupNoticeEvent",
    "GroupRecallNoticeEvent",
    "GroupDecreaseEvent",
    "GroupAdminNoticeEvent",
    "GroupIncreaseEvent",
    "GroupBanEvent",
    "GroupUploadNoticeEvent",
    "GroupCardEvent",
    "GroupNameEvent",
    "GroupTitleEvent",
    "GroupEssenceEvent",
    "GroupMsgEmojiLikeEvent",
    "GroupPokeEvent",
    "GroupLuckyKingEvent",
    "GroupHonorEvent",
    "FriendAddNoticeEvent",
    "FriendRecallNoticeEvent",
    "FriendPokeEvent",
    "ProfileLikeEvent",
    "InputStatusEvent",
    "BotOfflineEvent",
    # request_event
    "RequestEvent",
    "Request",
    "FriendRequestEvent",
    "GroupRequestEvent",
]
