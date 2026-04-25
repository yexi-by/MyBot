"""NapCat 请求事件模型。"""

from typing import Annotated, Literal

from pydantic import Field

from app.models.common import NapCatId, NapCatModel


class Request(NapCatModel):
    """请求事件基类。"""

    time: int
    self_id: NapCatId
    user_id: NapCatId
    comment: str = ""
    flag: str
    post_type: Literal["request"]


class FriendRequestEvent(Request):
    """好友添加请求事件。"""

    request_type: Literal["friend"]


class GroupRequestEvent(Request):
    """加群请求或邀请事件。"""

    group_id: NapCatId
    request_type: Literal["group"]
    sub_type: Literal["add", "invite"] | str


type RequestEvent = Annotated[
    FriendRequestEvent | GroupRequestEvent, Field(discriminator="request_type")
]
