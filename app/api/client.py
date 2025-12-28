from typing import Any, Literal, overload

from fastapi import WebSocket

from app.models import (
    At,
    Dice,
    Face,
    File,
    Image,
    MessageSegment,
    Record,
    Reply,
    Rps,
    Text,
    Video,
)
from app.models.api import (
    GroupMessageParams,
    GroupMessagePayload,
    PrivateMessageParams,
    PrivateMessagePayload,
)


class BOTClient:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    @overload
    async def send_msg(
        self, *, group_id: int, message_segment: list[MessageSegment] | None = None
    ) -> None: ...
    @overload
    async def send_msg(
        self,
        *,
        group_id: int,
        text: str | None = None,
        at: int | Literal["all"] | None = None,
        image: str | None = None,
        reply: int | None = None,
        face: int | None = None,
        dice: bool | None = None,
        rps: bool | None = None,
        file: str | None = None,
        video: str | None = None,
        record: str | None = None,
    ) -> None: ...

    @overload
    async def send_msg(
        self, *, user_id: int, message_segment: list[MessageSegment] | None = None
    ) -> None: ...
    @overload
    async def send_msg(
        self,
        *,
        user_id: int,
        text: str | None = None,
        image: str | None = None,
        reply: int | None = None,
        face: int | None = None,
        dice: bool | None = None,
        rps: bool | None = None,
        file: str | None = None,
        video: str | None = None,
        record: str | None = None,
    ) -> None: ...

    async def send_msg(
        self,
        *,
        group_id: int | None = None,
        user_id: int | None = None,
        message_segment: list[MessageSegment] | None = None,
        text: str | None = None,
        at: int | Literal["all"] | None = None,
        image: str | None = None,
        reply: int | None = None,
        face: int | None = None,
        dice: bool | None = None,
        rps: bool | None = None,
        file: str | None = None,
        video: str | None = None,
        record: str | None = None,
    ) -> None:
        mapping: list[tuple[Any, type[MessageSegment]]] = [
            (text, Text),
            (at, At),
            (image, Image),
            (reply, Reply),
            (face, Face),
            (dice, Dice),
            (rps, Rps),
            (file, File),
            (video, Video),
            (record, Record),
        ]
        if message_segment is None:
            message_segment = [
                cls.new(value) for value, cls in mapping if value is not None
            ]
        if user_id is not None:
            for Segment in message_segment:
                if isinstance(Segment, At):
                    raise ValueError("私聊消息不能包含 At 段")
            payload = PrivateMessagePayload(
                params=PrivateMessageParams(user_id=user_id, message=message_segment)
            )
        elif group_id is not None:
            payload = GroupMessagePayload(
                params=GroupMessageParams(group_id=group_id, message=message_segment)
            )
        else:
            raise ValueError("必须指定 user_id 或 group_id 其一")
        await self.websocket.send_text(payload.model_dump_json())
