from typing import Literal
from pydantic import BaseModel
from ..segments import MessageSegment

class PrivateMessageParams(BaseModel):
    user_id: int
    message: list[MessageSegment]


class PrivateMessagePayload(BaseModel):
    action: Literal["send_private_msg"] = "send_private_msg"
    params: PrivateMessageParams


class GroupMessageParams(BaseModel):
    group_id: int
    message: list[MessageSegment]


class GroupMessagePayload(BaseModel):
    action: Literal["send_group_msg"] = "send_group_msg"
    params: GroupMessageParams