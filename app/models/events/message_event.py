"""NapCat 消息事件模型。"""

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from app.models.common import JsonValue, NapCatId, NapCatModel, NapCatStringInteger

from ..segments import MessageSegment


def normalize_message_segments(value: object) -> object:
    """把 string 格式消息归一化为单个 text 消息段。"""
    # Pydantic 入站钩子只能接收 object；这里在协议边界立即收窄。
    if isinstance(value, str):
        return [{"type": "text", "data": {"text": value}}]
    return value


type MessageSegments = Annotated[
    list[MessageSegment], BeforeValidator(normalize_message_segments)
]


class Sender(NapCatModel):
    """消息发送者信息。"""

    user_id: NapCatId
    nickname: str = ""
    card: str | None = None
    sex: Literal["male", "female", "unknown"] | None = None
    age: int | None = None
    area: str | None = None
    level: str | None = None
    role: Literal["owner", "admin", "member"] | str | None = None
    title: str | None = None


class BaseMessage(NapCatModel):
    """消息事件公共字段。"""

    time: int
    self_id: NapCatId
    post_type: Literal["message", "message_sent"]
    sub_type: str = "normal"
    user_id: NapCatId
    message_id: NapCatId
    message: MessageSegments
    raw_message: str = ""
    sender: Sender
    font: NapCatStringInteger | None = None
    message_format: Literal["array", "string"] | str | None = None
    message_seq: NapCatId | None = None
    real_id: NapCatId | None = None
    target_id: NapCatId | None = None
    raw: JsonValue = None


class GroupMessage(BaseMessage):
    """群消息事件。"""

    message_type: Literal["group"]
    sub_type: Literal["normal", "anonymous", "notice"] | str = "normal"
    group_id: NapCatId
    group_name: str | None = None


class PrivateMessage(BaseMessage):
    """私聊消息事件。"""

    message_type: Literal["private"]
    sub_type: Literal["friend", "group", "other"] | str = "friend"
    group_id: NapCatId | None = None
    temp_source: int | None = None


type MessageEvent = Annotated[
    GroupMessage | PrivateMessage, Field(discriminator="message_type")
]
