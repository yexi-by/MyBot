from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..segments import MessageSegment


class Sender(BaseModel):
    """消息发送者信息"""

    user_id: int  # 发送者 QQ 号
    nickname: str  # 昵称
    card: str | None = None  # 群名片/备注 (仅群消息有效)
    role: str | None = None  # 群角色: owner/admin/member (仅群消息有效)


class BaseMessage(BaseModel):
    """消息事件基类"""

    time: int  # 事件发生的 Unix 时间戳
    post_type: Literal["message"]
    self_id: int  # 收到事件的机器人 QQ 号
    user_id: int  # 发送者 QQ 号
    message_id: int  # 消息 ID
    sender: Sender  # 发送者信息
    message: list[MessageSegment]  # 消息内容
    # raw_message: str = ""  # CQ 码格式的原始消息 弃用
    # font: int = 0  # 字体 (通常为 0，已弃用)


class GroupMessage(BaseMessage):
    """群消息事件"""

    message_type: Literal["group"]
    sub_type: Literal["normal", "anonymous", "notice"] = "normal"
    # normal: 普通消息, anonymous: 匿名消息, notice: 系统提示
    group_id: int  # 群号
    group_name: str  # 群名称 (NapCat 扩展字段)


class PrivateMessage(BaseMessage):
    """私聊消息事件"""

    message_type: Literal["private"]
    sub_type: Literal["friend", "group", "other"] = "friend"
    # friend: 好友私聊, group: 群临时会话, other: 其他


type MessageEvent = Annotated[
    GroupMessage | PrivateMessage, Field(discriminator="message_type")
]
