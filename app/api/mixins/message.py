"""NapCat 消息相关 Action。"""

from typing import Literal, overload

from app.models import (
    At,
    Dice,
    Face,
    File,
    Image,
    MessageSegment,
    NapCatId,
    Record,
    Reply,
    Response,
    Rps,
    Text,
    Video,
)

from .base import BaseMixin


class MessageMixin(BaseMixin):
    """消息相关 API。"""

    @overload
    async def send_msg(
        self,
        *,
        group_id: NapCatId,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """通过消息段发送群消息。"""
        ...

    @overload
    async def send_msg(
        self,
        *,
        group_id: NapCatId,
        text: str | None = None,
        at: NapCatId | Literal["all"] | None = None,
        image: str | None = None,
        reply: NapCatId | None = None,
        face: NapCatId | None = None,
        dice: bool | None = None,
        rps: bool | None = None,
        file: str | None = None,
        video: str | None = None,
        record: str | None = None,
    ) -> Response:
        """通过快捷参数发送群消息。"""
        ...

    @overload
    async def send_msg(
        self,
        *,
        user_id: NapCatId,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """通过消息段发送私聊消息。"""
        ...

    @overload
    async def send_msg(
        self,
        *,
        user_id: NapCatId,
        text: str | None = None,
        image: str | None = None,
        reply: NapCatId | None = None,
        face: NapCatId | None = None,
        dice: bool | None = None,
        rps: bool | None = None,
        file: str | None = None,
        video: str | None = None,
        record: str | None = None,
    ) -> Response:
        """通过快捷参数发送私聊消息。"""
        ...

    async def send_msg(
        self,
        *,
        group_id: NapCatId | None = None,
        user_id: NapCatId | None = None,
        message_segment: list[MessageSegment] | None = None,
        text: str | None = None,
        at: NapCatId | Literal["all"] | None = None,
        image: str | None = None,
        reply: NapCatId | None = None,
        face: NapCatId | None = None,
        dice: bool | None = None,
        rps: bool | None = None,
        file: str | None = None,
        video: str | None = None,
        record: str | None = None,
    ) -> Response:
        """发送私聊或群聊消息。"""
        if message_segment is None:
            message_segment = []
            if text is not None:
                message_segment.append(Text.new(text))
            if at is not None:
                message_segment.append(At.new(at))
            if image is not None:
                message_segment.append(Image.new(image))
            if reply is not None:
                message_segment.append(Reply.new(reply))
            if face is not None:
                message_segment.append(Face.new(face))
            if dice is not None:
                message_segment.append(Dice.new(dice))
            if rps is not None:
                message_segment.append(Rps.new(rps))
            if file is not None:
                message_segment.append(File.new(file))
            if video is not None:
                message_segment.append(Video.new(video))
            if record is not None:
                message_segment.append(Record.new(record))

        if user_id is None and group_id is None:
            raise ValueError("必须指定 user_id 或 group_id 其一")
        if user_id is not None and group_id is not None:
            raise ValueError("不能同时指定 user_id 和 group_id")
        if user_id is not None:
            if any(isinstance(segment, At) for segment in message_segment):
                raise ValueError("私聊消息不能包含 At 段")
            params = self._build_params(
                message_type="private", user_id=user_id, message=message_segment
            )
        else:
            params = self._build_params(
                message_type="group", group_id=group_id, message=message_segment
            )
        return await self._call_action("send_msg", params)

    async def delete_msg(self, message_id: NapCatId) -> None:
        """撤回消息。"""
        await self._send_action("delete_msg", self._build_params(message_id=message_id))

    async def get_msg(self, message_id: NapCatId) -> Response:
        """获取消息详情。"""
        return await self._call_action("get_msg", self._build_params(message_id=message_id))

    async def get_forward_msg(self, message_id: NapCatId) -> Response:
        """获取合并转发消息。"""
        return await self._call_action(
            "get_forward_msg", self._build_params(message_id=message_id)
        )

    async def set_msg_emoji_like(
        self, message_id: NapCatId, emoji_id: NapCatId, set: bool = True
    ) -> None:
        """设置消息表情回应。"""
        await self._send_action(
            "set_msg_emoji_like",
            self._build_params(message_id=message_id, emoji_id=emoji_id, set=set),
        )

    async def fetch_emoji_like(
        self,
        message_id: NapCatId,
        emoji_id: str,
        emoji_type: str,
        count: int | None = None,
    ) -> Response:
        """获取消息表情回应详情。"""
        return await self._call_action(
            "fetch_emoji_like",
            self._build_params(
                message_id=message_id,
                emojiId=emoji_id,
                emojiType=emoji_type,
                count=count,
            ),
        )

    async def get_group_msg_history(
        self,
        group_id: NapCatId,
        message_seq: NapCatId | None = None,
        count: int = 20,
        reverse_order: bool = False,
    ) -> Response:
        """获取群历史消息。"""
        return await self._call_action(
            "get_group_msg_history",
            self._build_params(
                group_id=group_id,
                message_seq=message_seq,
                count=count,
                reverseOrder=reverse_order,
            ),
        )

    async def get_friend_msg_history(
        self,
        user_id: NapCatId,
        message_seq: NapCatId | None = None,
        count: int = 20,
        reverse_order: bool = False,
    ) -> Response:
        """获取好友历史消息。"""
        return await self._call_action(
            "get_friend_msg_history",
            self._build_params(
                user_id=user_id,
                message_seq=message_seq,
                count=count,
                reverseOrder=reverse_order,
            ),
        )

    async def get_record(
        self,
        file: str | None = None,
        file_id: str | None = None,
        out_format: str = "mp3",
    ) -> Response:
        """获取语音文件信息。"""
        return await self._call_action(
            "get_record",
            self._build_params(file=file, file_id=file_id, out_format=out_format),
        )

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """获取图片文件信息。"""
        return await self._call_action(
            "get_image", self._build_params(file_id=file_id, file=file)
        )

    async def send_poke(
        self,
        user_id: NapCatId,
        group_id: NapCatId | None = None,
        target_id: NapCatId | None = None,
    ) -> None:
        """发送戳一戳。"""
        await self._send_action(
            "send_poke",
            self._build_params(user_id=user_id, group_id=group_id, target_id=target_id),
        )

    async def forward_group_single_msg(
        self, group_id: NapCatId, message_id: NapCatId
    ) -> None:
        """转发单条消息到群。"""
        await self._send_action(
            "forward_group_single_msg",
            self._build_params(group_id=group_id, message_id=message_id),
        )

    async def forward_friend_single_msg(
        self, user_id: NapCatId, message_id: NapCatId
    ) -> None:
        """转发单条消息到私聊。"""
        await self._send_action(
            "forward_friend_single_msg",
            self._build_params(user_id=user_id, message_id=message_id),
        )

    async def group_poke(self, group_id: NapCatId, user_id: NapCatId) -> None:
        """发送群聊戳一戳。"""
        await self._send_action(
            "group_poke", self._build_params(group_id=group_id, user_id=user_id)
        )

    async def friend_poke(self, user_id: NapCatId) -> None:
        """发送私聊戳一戳。"""
        await self._send_action("friend_poke", self._build_params(user_id=user_id))
