from typing import Literal

from pydantic import BaseModel

from ..segments import MessageSegment


class PrivateMessageParams(BaseModel):
    user_id: int
    message: list[MessageSegment]


class PrivateMessagePayload(BaseModel):
    action: Literal["send_private_msg"] = "send_private_msg"
    params: PrivateMessageParams
    echo: str


class GroupMessageParams(BaseModel):
    group_id: int
    message: list[MessageSegment]


class GroupMessagePayload(BaseModel):
    action: Literal["send_group_msg"] = "send_group_msg"
    params: GroupMessageParams
    echo: str


class PokeParams(BaseModel):
    user_id: int
    group_id: int | None = None  # 不填则为私聊戳
    target_id: int | None = None  # 戳一戳对象


class SendPokePayload(BaseModel):
    """发送戳一戳"""

    action: Literal["send_poke"] = "send_poke"
    params: PokeParams


class DeleteMsgParams(BaseModel):
    message_id: int


class DeleteMsgPayload(BaseModel):
    action: Literal["delete_msg"] = "delete_msg"
    params: DeleteMsgParams

