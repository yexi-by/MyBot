from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Request(BaseModel):
    """请求事件基类"""

    time: int  # 事件发生的 Unix 时间戳
    self_id: int  # 收到事件的机器人 QQ 号
    user_id: int  # 发送请求的 QQ 号
    comment: str  # 验证信息/附加说明
    flag: str  # 请求 flag，处理请求时需要传回 (用于同意/拒绝)
    post_type: Literal["request"]


class FriendRequestEvent(Request):
    """好友添加请求事件 - 有人申请加你为好友"""

    request_type: Literal["friend"]


class GroupRequestEvent(Request):
    """加群请求/邀请事件"""

    group_id: int  # 群号
    request_type: Literal["group"]
    sub_type: Literal["add", "invite"]
    # add: 某人申请入群, invite: Bot 被邀请入群


type RequestEvent = Annotated[
    FriendRequestEvent | GroupRequestEvent, Field(discriminator="request_type")
]
