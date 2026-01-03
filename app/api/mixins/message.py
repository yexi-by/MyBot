"""消息相关 Mixin 类"""

import time
from typing import Any, Literal, overload

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
    SelfMessage,
    Text,
    Video,
)
from app.models.api.payloads import base as base_payload
from app.models.events.response import MessageData, Response

from .base import BaseMixin


class MessageMixin(BaseMixin):
    """消息相关的 API 接口"""

    @overload
    async def send_msg(
        self,
        *,
        group_id: int,
        message_segment: list[MessageSegment] | None = None,
    ) -> SelfMessage: ...

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
    ) -> SelfMessage: ...

    @overload
    async def send_msg(
        self,
        *,
        user_id: int,
        message_segment: list[MessageSegment] | None = None,
    ) -> SelfMessage: ...

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
    ) -> SelfMessage: ...

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
    ) -> SelfMessage:
        """发送消息 群聊|私人

        Args:
            group_id: 群号（与 user_id 二选一）
            user_id: 用户 QQ 号（与 group_id 二选一）
            message_segment: 消息段列表，优先使用
            text: 文本内容
            at: @某人的 QQ 号或 "all"
            image: 图片路径/URL/base64
            reply: 回复的消息 ID
            face: QQ 表情 ID
            dice: 是否发送骰子
            rps: 是否发送猜拳
            file: 文件路径
            video: 视频路径
            record: 语音路径

        Returns:
            SelfMessage: 发送成功后的消息对象

        Raises:
            ValueError: 未指定 user_id 或 group_id，或私聊消息包含 At 段
        """
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

        echo = self._generate_echo()
        time_id = int(time.time())

        match (user_id, group_id):
            case (int() as uid, None):
                # 私聊消息
                if any(isinstance(seg, At) for seg in message_segment):
                    raise ValueError("私聊消息不能包含 At 段")
                payload = base_payload.PrivateMessagePayload(
                    params=base_payload.PrivateMessageParams(
                        user_id=uid, message=message_segment
                    ),
                    echo=echo,
                )
            case (None, int() as gid):
                # 群聊消息
                payload = base_payload.GroupMessagePayload(
                    params=base_payload.GroupMessageParams(
                        group_id=gid, message=message_segment
                    ),
                    echo=echo,
                )
            case (None, None):
                raise ValueError("必须指定 user_id 或 group_id 其一")
            case _:
                raise ValueError("不能同时指定 user_id 和 group_id")

        await self._send_payload(payload)
        result = await self.create_future(echo=echo)
        data = result.data

        if not isinstance(data, MessageData):
            raise ValueError("严重错误: 响应数据类型不正确")

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

    async def send_poke(
        self, user_id: int, group_id: int | None = None, target_id: int | None = None
    ) -> None:
        """发送戳一戳"""
        payload = base_payload.SendPokePayload(
            params=base_payload.PokeParams(
                user_id=user_id, group_id=group_id, target_id=target_id
            )
        )
        await self._send_payload(payload)

    async def delete_msg(self, msg_id: int) -> None:
        """撤回消息"""
        payload = base_payload.DeleteMsgPayload(
            params=base_payload.DeleteMsgParams(message_id=msg_id)
        )
        await self._send_payload(payload)

    async def get_forward_msg(self, message_id: int) -> Response:
        """获取合并转发消息"""
        echo = self._generate_echo()
        payload = base_payload.ForwardMsgPayload(
            params=base_payload.ForwardMsgParams(message_id=message_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_msg_emoji_like(
        self, message_id: int, emoji_id: int, set: bool = True
    ) -> None:
        """贴表情"""
        payload = base_payload.SendEmojiPayload(
            params=base_payload.EmojiParams(
                message_id=message_id, emoji_id=emoji_id, set=set
            )
        )
        await self._send_payload(payload)

    async def get_msg(self, message_id: int) -> Response:
        """获取消息详情"""
        echo = self._generate_echo()
        payload = base_payload.GetMsgPayload(
            params=base_payload.GetMsgParams(message_id=message_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_msg_history(
        self,
        group_id: int,
        message_seq: int | None = None,
        count: int = 20,
        reverse_order: bool = False,
    ) -> Response:
        """获取群历史消息"""
        echo = self._generate_echo()
        payload = base_payload.GetGroupMsgHistoryPayload(
            params=base_payload.GetGroupMsgHistoryParams(
                group_id=group_id,
                message_seq=message_seq,
                count=count,
                reverseOrder=reverse_order,
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_friend_msg_history(
        self,
        user_id: int,
        message_seq: int | None = None,
        count: int = 20,
        reverse_order: bool = False,
    ) -> Response:
        """获取好友历史消息"""
        echo = self._generate_echo()
        payload = base_payload.GetFriendMsgHistoryPayload(
            params=base_payload.GetFriendMsgHistoryParams(
                user_id=user_id,
                message_seq=message_seq,
                count=count,
                reverseOrder=reverse_order,
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def fetch_emoji_like(
        self,
        message_id: int,
        emoji_id: str,
        emoji_type: str,
        count: int | None = None,
    ) -> Response:
        """获取贴表情详情"""
        echo = self._generate_echo()
        payload = base_payload.FetchEmojiLikePayload(
            params=base_payload.FetchEmojiLikeParams(
                message_id=message_id,
                emojiId=emoji_id,
                emojiType=emoji_type,
                count=count,
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_record(
        self,
        file: str | None = None,
        file_id: str | None = None,
        out_format: str = "mp3",
    ) -> Response:
        """获取语音消息详情"""
        echo = self._generate_echo()
        payload = base_payload.GetRecordPayload(
            params=base_payload.GetRecordParams(
                file=file, file_id=file_id, out_format=out_format
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """获取图片消息详情"""
        echo = self._generate_echo()
        payload = base_payload.GetImagePayload(
            params=base_payload.GetImageParams(file_id=file_id, file=file),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def forward_group_single_msg(self, group_id: int, message_id: int) -> None:
        """消息转发到群"""
        payload = base_payload.ForwardGroupSingleMsgPayload(
            params=base_payload.ForwardGroupSingleMsgParams(
                group_id=group_id, message_id=message_id
            )
        )
        await self._send_payload(payload)

    async def forward_friend_single_msg(self, user_id: int, message_id: int) -> None:
        """消息转发到私聊"""
        payload = base_payload.ForwardFriendSingleMsgPayload(
            params=base_payload.ForwardFriendSingleMsgParams(
                user_id=user_id, message_id=message_id
            )
        )
        await self._send_payload(payload)

    async def group_poke(self, group_id: int, user_id: int) -> None:
        """发送群聊戳一戳"""
        payload = base_payload.GroupPokePayload(
            params=base_payload.GroupPokeParams(group_id=group_id, user_id=user_id)
        )
        await self._send_payload(payload)

    async def friend_poke(self, user_id: int) -> None:
        """发送私聊戳一戳"""
        payload = base_payload.FriendPokePayload(
            params=base_payload.FriendPokeParams(user_id=user_id)
        )
        await self._send_payload(payload)
