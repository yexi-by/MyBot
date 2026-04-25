"""NapCat 元事件模型。"""

from typing import Annotated, Literal

from pydantic import Field

from app.models.common import JsonValue, NapCatId, NapCatModel


class HeartbeatStatus(NapCatModel):
    """心跳状态信息。"""

    online: bool | None = None
    good: bool = True
    stat: JsonValue = None


class Meta(NapCatModel):
    """元事件基类。"""

    time: int
    self_id: NapCatId
    post_type: Literal["meta_event"]


class LifeCycle(Meta):
    """生命周期事件。"""

    meta_event_type: Literal["lifecycle"]
    sub_type: Literal["enable", "disable", "connect"] | str


class HeartBeat(Meta):
    """心跳事件。"""

    meta_event_type: Literal["heartbeat"]
    status: HeartbeatStatus
    interval: int


type MetaEvent = Annotated[
    LifeCycle | HeartBeat, Field(discriminator="meta_event_type")
]
