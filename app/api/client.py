import asyncio
import time
import uuid
from typing import Any, Literal, overload

from fastapi import WebSocket

from app.database import RedisDatabaseManager
from app.models import (
    AllEvent,
    At,
    Dice,
    Face,
    File,
    Image,
    LifeCycle,
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
    PrivateMessageParams,
    PrivateMessagePayload,
    message,
)
from app.models.events.response import MessageData
from app.utils import logger


class BOTClient:
    def __init__(self, websocket: WebSocket, database: RedisDatabaseManager) -> None:
        self.websocket = websocket
        self.database = database
        self.echo_dict: dict[str, asyncio.Future[Response]] = {}
        self.boot_id: int = 0
        self.timeout: int = 20

    def get_self_qq_id(self, msg: AllEvent) -> None:
        """获取自身qq号,外部接口"""
        if isinstance(msg, LifeCycle):
            self.boot_id = msg.self_id

    def receive_data(self, response: Response) -> None:
        """对外接口,找到对应future并填充"""
        echo = response.echo
        if echo:
            future = self.echo_dict[echo]
            future.set_result(response)

    async def create_future(self, echo: str) -> Response:
        """创建future 监听future完成"""
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
        if not isinstance(data, MessageData):
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

    async def send_poke(
        self, user_id: int, group_id: int | None = None, target_id: int | None = None
    ) -> None:
        payload = message.SendPokePayload(
            params=message.PokeParams(
                user_id=user_id, group_id=group_id, target_id=target_id
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def delete_msg(self, msg_id: int):
        payload = message.DeleteMsgPayload(
            params=message.DeleteMsgParams(message_id=msg_id)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_forward_msg(self, message_id: int):
        echo = str(uuid.uuid4())
        payload = message.ForwardMsgPayload(
            params=message.ForwardMsgParams(message_id=message_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=payload.echo)
        return result

    async def set_msg_emoji_like(
        self, message_id: int, emoji_id: int, set: bool = True
    ):
        """贴表情"""
        payload = message.SendEmojiPayload(
            params=message.EmojiParams(
                message_id=message_id, emoji_id=emoji_id, set=set
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def send_group_ai_record(
        self,
        group_id: int,
        character: str,
        text: str,
    ) -> None:
        """发送群AI语音"""
        payload = message.GroupAiRecordPayload(
            params=message.GroupAiRecordParams(
                group_id=group_id, character=character, text=text
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_group_detail_info(self, group_id: int):
        """获取群信息"""
        echo = str(uuid.uuid4())
        payload = message.GroupDetailInfoPayload(
            params=message.GroupDetailInfoParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=payload.echo)
        return result

    async def set_group_kick(
        self,
        group_id: int,
        user_id: int,
        reject_add_request: bool = False,
    ) -> None:
        """踢出群成员"""
        payload = message.GroupKickPayload(
            params=message.GroupKickParams(
                group_id=group_id,
                user_id=user_id,
                reject_add_request=reject_add_request,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_ban(
        self,
        group_id: int,
        user_id: int,
        duration: int,
    ) -> None:
        """群禁言"""
        payload = message.GroupBanPayload(
            params=message.GroupBanParameters(
                group_id=group_id,
                user_id=user_id,
                duration=duration,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_essence_msg_list(self, group_id: int):
        """获取群精华消息"""
        echo = str(uuid.uuid4())
        payload = message.GetEssenceMsgListPayload(
            params=message.GetEssenceMsgListParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def set_group_whole_ban(self, group_id: int, enable: bool) -> None:
        """全体禁言"""
        payload = message.SetGroupWholeBanPayload(
            params=message.SetGroupWholeBanParams(group_id=group_id, enable=enable)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_portrait(self, group_id: int, file: str) -> None:
        """设置群头像"""
        payload = message.SetGroupPortraitPayload(
            params=message.SetGroupPortraitParams(group_id=group_id, file=file)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_admin(self, group_id: int, user_id: int, enable: bool) -> None:
        """设置群管理"""
        payload = message.SetGroupAdminPayload(
            params=message.SetGroupAdminParams(
                group_id=group_id, user_id=user_id, enable=enable
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_card(
        self, group_id: int, user_id: int, card: str | None = None
    ) -> None:
        """设置群成员名片"""
        payload = message.SetGroupCardPayload(
            params=message.SetGroupCardParams(
                group_id=group_id, user_id=user_id, card=card
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_essence_msg(self, message_id: int) -> None:
        """设置群精华消息"""
        payload = message.SetEssenceMsgPayload(
            params=message.SetEssenceMsgParams(message_id=message_id)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_name(self, group_id: int, group_name: str) -> None:
        """设置群名"""
        payload = message.SetGroupNamePayload(
            params=message.SetGroupNameParams(group_id=group_id, group_name=group_name)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def delete_essence_msg(self, message_id: int) -> None:
        """删除群精华消息"""
        payload = message.DeleteEssenceMsgPayload(
            params=message.DeleteEssenceMsgParams(message_id=message_id)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def del_group_notice(self, group_id: int, notice_id: str) -> None:
        """删除群公告"""
        payload = message.DelGroupNoticePayload(
            params=message.DelGroupNoticeParams(group_id=group_id, notice_id=notice_id)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_leave(
        self, group_id: int, is_dismiss: bool | None = None
    ) -> None:
        """退群"""
        payload = message.SetGroupLeavePayload(
            params=message.SetGroupLeaveParams(group_id=group_id, is_dismiss=is_dismiss)
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def send_group_notice(
        self,
        group_id: int,
        content: str,
        image: str | None = None,
        pinned: int | None = None,
        type: int | None = None,
        confirm_required: int | None = None,
        is_show_edit_card: int | None = None,
        tip_window_type: int | None = None,
    ) -> None:
        """发送群公告"""
        payload = message.SendGroupNoticePayload(
            params=message.SendGroupNoticeParams(
                group_id=group_id,
                content=content,
                image=image,
                pinned=pinned,
                type=type,
                confirm_required=confirm_required,
                is_show_edit_card=is_show_edit_card,
                tip_window_type=tip_window_type,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_search(
        self,
        group_id: int,
        no_code_finger_open: int | None = None,
        no_finger_open: int | None = None,
    ) -> None:
        """设置群搜索"""
        payload = message.SetGroupSearchPayload(
            params=message.SetGroupSearchParams(
                group_id=group_id,
                no_code_finger_open=no_code_finger_open,
                no_finger_open=no_finger_open,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_group_notice(self, group_id: int):
        """获取群公告"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupNoticePayload(
            params=message.GetGroupNoticeParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def set_group_add_request(
        self, flag: str, approve: bool, reason: str | None = None
    ) -> None:
        """处理加群请求"""
        payload = message.SetGroupAddRequestPayload(
            params=message.SetGroupAddRequestParams(
                flag=flag, approve=approve, reason=reason
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_group_info(self, group_id: int):
        """获取群信息"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupInfoPayload(
            params=message.GetGroupInfoParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_list(self, no_cache: bool = False):
        """获取群列表"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupListPayload(
            params=message.GetGroupListParams(no_cache=no_cache),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_member_info(
        self, group_id: int, user_id: int, no_cache: bool = False
    ):
        """获取群成员信息"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupMemberInfoPayload(
            params=message.GetGroupMemberInfoParams(
                group_id=group_id, user_id=user_id, no_cache=no_cache
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_member_list(self, group_id: int, no_cache: bool = False):
        """获取群成员列表"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupMemberListPayload(
            params=message.GetGroupMemberListParams(
                group_id=group_id, no_cache=no_cache
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_honor_info(self, group_id: int, type: str = "all"):
        """获取群荣誉"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupHonorInfoPayload(
            params=message.GetGroupHonorInfoParams(group_id=group_id, type=type),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_at_all_remain(self, group_id: int):
        """获取群@全体成员剩余次数"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupAtAllRemainPayload(
            params=message.GetGroupAtAllRemainParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_shut_list(self, group_id: int):
        """获取群禁言列表"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupShutListPayload(
            params=message.GetGroupShutListParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def set_group_sign(self, group_id: int) -> None:
        """群打卡"""
        payload = message.SetGroupSignPayload(
            params=message.SetGroupSignParams(group_id=str(group_id))
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_todo(
        self, group_id: int, message_id: int, message_seq: str | None = None
    ) -> None:
        """设置群代办"""
        payload = message.SetGroupTodoPayload(
            params=message.SetGroupTodoParams(
                group_id=str(group_id),
                message_id=str(message_id),
                message_seq=message_seq,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    # ==================== 文件相关 ====================

    async def upload_group_file(
        self,
        group_id: int,
        file: str,
        name: str,
        folder: str | None = None,
        folder_id: str | None = None,
    ) -> None:
        """上传群文件"""
        payload = message.UploadGroupFilePayload(
            params=message.UploadGroupFileParams(
                group_id=group_id,
                file=file,
                name=name,
                folder=folder,
                folder_id=folder_id,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def upload_private_file(
        self,
        user_id: int,
        file: str,
        name: str,
    ) -> None:
        """上传私聊文件"""
        payload = message.UploadPrivateFilePayload(
            params=message.UploadPrivateFileParams(
                user_id=user_id,
                file=file,
                name=name,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_group_root_files(self, group_id: int, file_count: int = 50):
        """获取群根目录文件列表"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupRootFilesPayload(
            params=message.GetGroupRootFilesParams(
                group_id=group_id, file_count=file_count
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_files_by_folder(
        self,
        group_id: int,
        folder_id: str | None = None,
        folder: str | None = None,
        file_count: int = 50,
    ):
        """获取群子目录文件列表"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupFilesByFolderPayload(
            params=message.GetGroupFilesByFolderParams(
                group_id=group_id,
                folder_id=folder_id,
                folder=folder,
                file_count=file_count,
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_file_system_info(self, group_id: int):
        """获取群文件系统信息"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupFileSystemInfoPayload(
            params=message.GetGroupFileSystemInfoParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_file(self, file_id: str | None = None, file: str | None = None):
        """获取文件信息"""
        echo = str(uuid.uuid4())
        payload = message.GetFilePayload(
            params=message.GetFileParams(file_id=file_id, file=file),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_group_file_url(self, group_id: int, file_id: str):
        """获取群文件链接"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupFileUrlPayload(
            params=message.GetGroupFileUrlParams(group_id=group_id, file_id=file_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_private_file_url(self, file_id: str):
        """获取私聊文件链接"""
        echo = str(uuid.uuid4())
        payload = message.GetPrivateFileUrlPayload(
            params=message.GetPrivateFileUrlParams(file_id=file_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def create_group_file_folder(self, group_id: int, folder_name: str):
        """创建群文件文件夹"""
        echo = str(uuid.uuid4())
        payload = message.CreateGroupFileFolderPayload(
            params=message.CreateGroupFileFolderParams(
                group_id=group_id, folder_name=folder_name
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def delete_group_file(self, group_id: int, file_id: str):
        """删除群文件"""
        echo = str(uuid.uuid4())
        payload = message.DeleteGroupFilePayload(
            params=message.DeleteGroupFileParams(group_id=group_id, file_id=file_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def delete_group_folder(self, group_id: int, folder_id: str):
        """删除群文件夹"""
        echo = str(uuid.uuid4())
        payload = message.DeleteGroupFolderPayload(
            params=message.DeleteGroupFolderParams(
                group_id=group_id, folder_id=folder_id
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def move_group_file(
        self,
        group_id: int,
        file_id: str,
        current_parent_directory: str,
        target_parent_directory: str,
    ):
        """移动群文件"""
        echo = str(uuid.uuid4())
        payload = message.MoveGroupFilePayload(
            params=message.MoveGroupFileParams(
                group_id=group_id,
                file_id=file_id,
                current_parent_directory=current_parent_directory,
                target_parent_directory=target_parent_directory,
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def rename_group_file(
        self,
        group_id: int,
        file_id: str,
        current_parent_directory: str,
        new_name: str,
    ):
        """重命名群文件"""
        echo = str(uuid.uuid4())
        payload = message.RenameGroupFilePayload(
            params=message.RenameGroupFileParams(
                group_id=group_id,
                file_id=file_id,
                current_parent_directory=current_parent_directory,
                new_name=new_name,
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    # ==================== 群相册相关 ====================

    async def del_group_album_media(
        self, group_id: str, album_id: str, lloc: str
    ) -> None:
        """删除群相册文件"""
        payload = message.DelGroupAlbumMediaPayload(
            params=message.DelGroupAlbumMediaParams(
                group_id=group_id, album_id=album_id, lloc=lloc
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def set_group_album_media_like(
        self,
        group_id: str,
        album_id: str,
        lloc: str,
        id: str,
        set: bool = True,
    ) -> None:
        """点赞群相册"""
        payload = message.SetGroupAlbumMediaLikePayload(
            params=message.SetGroupAlbumMediaLikeParams(
                group_id=group_id,
                album_id=album_id,
                lloc=lloc,
                id=id,
                set=set,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def do_group_album_comment(
        self, group_id: str, album_id: str, lloc: str, content: str
    ) -> None:
        """查看群相册评论"""
        payload = message.DoGroupAlbumCommentPayload(
            params=message.DoGroupAlbumCommentParams(
                group_id=group_id, album_id=album_id, lloc=lloc, content=content
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_group_album_media_list(
        self, group_id: str, album_id: str, attach_info: str
    ):
        """获取群相册列表"""
        echo = str(uuid.uuid4())
        payload = message.GetGroupAlbumMediaListPayload(
            params=message.GetGroupAlbumMediaListParams(
                group_id=group_id, album_id=album_id, attach_info=attach_info
            ),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def upload_image_to_qun_album(
        self, group_id: str, album_id: str, album_name: str, file: str
    ) -> None:
        """上传图片到群相册"""
        payload = message.UploadImageToQunAlbumPayload(
            params=message.UploadImageToQunAlbumParams(
                group_id=group_id,
                album_id=album_id,
                album_name=album_name,
                file=file,
            )
        )
        await self.websocket.send_text(payload.model_dump_json())

    async def get_qun_album_list(self, group_id: str):
        """获取群相册总列表"""
        echo = str(uuid.uuid4())
        payload = message.GetQunAlbumListPayload(
            params=message.GetQunAlbumListParams(group_id=group_id),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result

    async def get_ai_characters(self, group_id: int, chat_type: Literal[1, 2] = 1):
        """获取群AI角色列表"""
        echo = str(uuid.uuid4())
        payload = message.AiCharactersPayload(
            params=message.AiCharactersParams(group_id=group_id, chat_type=chat_type),
            echo=echo,
        )
        await self.websocket.send_text(payload.model_dump_json())
        result = await self.create_future(echo=echo)
        return result
