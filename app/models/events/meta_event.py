from typing import Annotated, Literal

from pydantic import BaseModel, Field


class HeartbeatStatus(BaseModel):
    """心跳状态信息"""

    online: bool | None = None  # 当前 QQ 在线状态，None 表示无法获取
    good: bool = True  # 状态是否正常


class Meta(BaseModel):
    """元事件基类"""

    time: int  # 事件发生的 Unix 时间戳
    self_id: int  # 收到事件的机器人 QQ 号
    post_type: Literal["meta_event"]


class LifeCycle(Meta):
    """生命周期事件 - Bot 连接/启用/禁用时触发"""

    meta_event_type: Literal["lifecycle"]
    sub_type: Literal["enable", "disable", "connect"]
    # enable: OneBot 启用, disable: OneBot 禁用, connect: WebSocket 连接成功


class HeartBeat(Meta):
    """心跳事件 - 定期上报 Bot 状态"""

    meta_event_type: Literal["heartbeat"]
    status: HeartbeatStatus  # 状态信息
    interval: int  # 到下次心跳的间隔 (毫秒)


type MetaEvent = Annotated[
    LifeCycle | HeartBeat, Field(discriminator="meta_event_type")
]
