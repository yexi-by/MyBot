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
from app.models.common import JsonObject, JsonValue
from app.utils.log import log_event
from app.utils.retry_utils import create_retry_manager

from .base import BaseMixin


class NapCatSendMessageError(RuntimeError):
    """NapCat send_msg 返回失败或超时时抛出的显式异常。"""

    def __init__(self, message: str, *, response: Response | None = None) -> None:
        """保存错误摘要与可选 NapCat 响应。"""
        super().__init__(message)
        self.response: Response | None = response

    @classmethod
    def from_response(cls, *, response: Response) -> "NapCatSendMessageError":
        """根据 NapCat 失败响应构造异常。"""
        return cls(
            "NapCat 发送消息失败: "
            f"status={response.status!r} "
            f"retcode={response.retcode} "
            f"message={response.message!r} "
            f"wording={response.wording!r} "
            f"data={_summarize_response_data(response.data)}",
            response=response,
        )

    @classmethod
    def from_timeout(cls, *, error: TimeoutError) -> "NapCatSendMessageError":
        """根据等待 NapCat 回包超时构造异常。"""
        return cls(f"NapCat 发送消息失败: {error}")


def _summarize_response_data(data: JsonValue) -> str:
    """把 NapCat 响应 data 压缩成适合日志与异常的短文本。"""
    summary = repr(data)
    max_length = 500
    if len(summary) <= max_length:
        return summary
    return f"{summary[:max_length]}..."


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
        response = await self._call_send_msg_with_retry(params=params)
        await self._store_sent_message(
            response=response,
            group_id=group_id,
            user_id=user_id,
            message_segment=message_segment,
        )
        return response

    async def _call_send_msg_with_retry(self, *, params: JsonObject) -> Response:
        """调用 NapCat send_msg，并对可恢复发送失败执行配置化重试。"""
        retrier = create_retry_manager(
            error_types=(NapCatSendMessageError,),
            retry_count=self.send_retry_count,
            retry_delay=self.send_retry_delay,
        )
        async for attempt in retrier:
            with attempt:
                try:
                    response = await self._call_action("send_msg", params)
                except TimeoutError as exc:
                    raise NapCatSendMessageError.from_timeout(error=exc) from exc
                if response.status != "ok" or response.retcode != 0:
                    raise NapCatSendMessageError.from_response(response=response)
                return response
        raise RuntimeError("NapCat send_msg 重试流程异常结束")

    async def _store_sent_message(
        self,
        *,
        response: Response,
        message_segment: list[MessageSegment],
        group_id: NapCatId | None,
        user_id: NapCatId | None,
    ) -> None:
        """在发送成功后保存出站消息，支持后续引用机器人自己的消息。"""
        if response.status != "ok" or response.retcode != 0:
            return
        if not self.boot_id:
            log_event(
                level="WARNING",
                event="napcat.sent_message.self_id_missing",
                category="napcat_api",
                message="发送成功但机器人 self_id 尚未初始化，无法保存出站消息",
            )
            return
        message_id = self._extract_sent_message_id(response=response)
        if message_id is None:
            log_event(
                level="WARNING",
                event="napcat.sent_message.message_id_missing",
                category="napcat_api",
                message="发送成功但 NapCat 响应缺少 message_id，无法保存出站消息",
                response_data=response.data,
            )
            return
        await self.database.store_outgoing_message(
            self_id=self.boot_id,
            message_id=message_id,
            group_id=group_id,
            user_id=user_id,
            message_segments=message_segment,
        )

    def _extract_sent_message_id(self, *, response: Response) -> NapCatId | None:
        """从 NapCat send_msg 响应中提取消息 ID。"""
        data = response.data
        if not isinstance(data, dict):
            return None
        response_data: JsonObject = data
        raw_message_id = response_data.get("message_id")
        if isinstance(raw_message_id, bool):
            return None
        if isinstance(raw_message_id, int):
            return str(raw_message_id)
        if isinstance(raw_message_id, str):
            message_id = raw_message_id.strip()
            if message_id:
                return message_id
        return None

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
        emoji_id: NapCatId,
        emoji_type: NapCatId,
        count: int = 20,
        cookie: str = "",
    ) -> Response:
        """获取消息表情回应详情。"""
        return await self._call_action(
            "fetch_emoji_like",
            self._build_params(
                message_id=message_id,
                emojiId=emoji_id,
                emojiType=emoji_type,
                count=count,
                cookie=cookie,
            ),
        )

    async def get_group_msg_history(
        self,
        group_id: NapCatId,
        message_seq: NapCatId | None = None,
        count: int = 20,
        reverse_order: bool = False,
        disable_get_url: bool = False,
        parse_mult_msg: bool = True,
        quick_reply: bool = False,
    ) -> Response:
        """获取群历史消息。"""
        return await self._call_action(
            "get_group_msg_history",
            self._build_params(
                group_id=group_id,
                message_seq=message_seq,
                count=count,
                reverse_order=reverse_order,
                reverseOrder=reverse_order,
                disable_get_url=disable_get_url,
                parse_mult_msg=parse_mult_msg,
                quick_reply=quick_reply,
            ),
        )

    async def get_friend_msg_history(
        self,
        user_id: NapCatId,
        message_seq: NapCatId | None = None,
        count: int = 20,
        reverse_order: bool = False,
        disable_get_url: bool = False,
        parse_mult_msg: bool = True,
        quick_reply: bool = False,
    ) -> Response:
        """获取好友历史消息。"""
        return await self._call_action(
            "get_friend_msg_history",
            self._build_params(
                user_id=user_id,
                message_seq=message_seq,
                count=count,
                reverse_order=reverse_order,
                reverseOrder=reverse_order,
                disable_get_url=disable_get_url,
                parse_mult_msg=parse_mult_msg,
                quick_reply=quick_reply,
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
