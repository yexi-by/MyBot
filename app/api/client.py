import asyncio
import time
import uuid
from typing import Any, Literal, overload

from fastapi import WebSocket

from app.database import RedisDatabaseManager
from app.models import (
    At,
    Dice,
    Face,
    File,
    Image,
    MessageSegment,
    Record,
    Reply,
    Response,
    Rps,
    SelfMessage,
    Text,
    Video,
)
from app.models.api import (
    GroupMessageParams,
    GroupMessagePayload,
    LoginInfo,
    PrivateMessageParams,
    PrivateMessagePayload,
)
from app.models.events.response import IDData, SelfData
from app.utils import logger


class BOTClient:
    def __init__(self, websocket: WebSocket, database: RedisDatabaseManager) -> None:
        self.websocket = websocket
        self.database = database
        self.echo_dict: dict[str, asyncio.Future[Response]] = {}
        self.boot_id: int = 1
        self.timeout: int = 20
        asyncio.create_task(self.get_login_info())

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
    ) -> None | SelfMessage:
        """发送消息 群聊|私人"""
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
        echo = str(uuid.uuid4())
        time_id = int(time.time())
        if user_id is not None:
            for Segment in message_segment:
                if isinstance(Segment, At):
                    raise ValueError("私聊消息不能包含 At 段")
            payload = PrivateMessagePayload(
                params=PrivateMessageParams(user_id=user_id, message=message_segment),
                echo=echo,
            )

        elif group_id is not None:
            payload = GroupMessagePayload(
                params=GroupMessageParams(group_id=group_id, message=message_segment),
                echo=echo,
            )
        else:
            raise ValueError("必须指定 user_id 或 group_id 其一")
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        data = result.data
        if not isinstance(data, IDData):
            raise ValueError("严重错误")
        self_message = SelfMessage(
            message_id=data.message_id,
            self_id=self.boot_id,
            group_id=group_id,
            user_id=user_id,
            time=time_id,
            message=message_segment,
        )
        await self.database.add_to_queue(msg=self_message)
        return self_message

    async def get_login_info(self) -> None:
        """获取自身qq号"""
        try:
            echo = str(uuid.uuid4())
            payload = LoginInfo(echo=echo)
            await self.websocket.send_text(payload.model_dump_json())
            result = await self.create_future(echo=echo)
            data = result.data
            if not isinstance(data, SelfData):
                raise ValueError("严重错误")
            self.boot_id = data.user_id
        except asyncio.CancelledError:
            logger.debug("获取登录信息任务被取消")
        except Exception as e:
            logger.warning(f"获取登录信息失败（WebSocket可能已断开）: {e}")

    def receive_data(self, response: Response) -> None:
        """对外接口,找到对应future并填充"""
        future = self.echo_dict[response.echo]
        future.set_result(response)

    async def create_future(self, echo: str) -> Response:
        """监听future完成"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Response] = loop.create_future()
        self.echo_dict[echo] = future
        try:
            await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            del self.echo_dict[echo]
            logger.error(f"等待响应超时 (echo={echo}, timeout={self.timeout}s)")
            raise ValueError(f"严重错误: 等待响应超时 (echo={echo})")
        result = future.result()
        del self.echo_dict[echo]
        return result
