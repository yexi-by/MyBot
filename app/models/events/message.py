from typing import Literal, Annotated
from pydantic import BaseModel, Field
from ..segments import MessageSegment


class Sender(BaseModel):
    user_id: int
    nickname: str
    card: str | None = None
    role: str | None = None


class BaseMessage(BaseModel):
    post_type: Literal["message"]
    self_id: int
    user_id: int
    message_id: int
    sender: Sender
    message: list[MessageSegment]


class GroupMessage(BaseMessage):
    message_type: Literal["group"]
    group_id: int
    group_name: str


class PrivateMessage(BaseMessage):
    message_type: Literal["private"]
    sub_type: Literal["friend", "group"]


type MessageEvent = Annotated[
    GroupMessage | PrivateMessage, Field(discriminator="message_type")
]

type AllEvent = Annotated[MessageEvent, Field(discriminator="post_type")]
