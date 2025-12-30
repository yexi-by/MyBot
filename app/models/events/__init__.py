from pydantic import Field
from typing import Annotated
from .message_event import GroupMessage, PrivateMessage, MessageEvent
from .meta_event import MetaEvent
from .notice_event import NoticeEvent
from .request_event import RequestEvent

type AllEvent = Annotated[
    MessageEvent | MetaEvent | NoticeEvent | RequestEvent,
    Field(discriminator="post_type"),
]
__all__ = ["AllEvent", "GroupMessage", "PrivateMessage"]
