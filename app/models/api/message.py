from typing import Literal

from pydantic import BaseModel

from ..segments import MessageSegment


class PrivateMessageParams(BaseModel):
    user_id: int
    message: list[MessageSegment]


class PrivateMessagePayload(BaseModel):
    action: Literal["send_private_msg"] = "send_private_msg"
    params: PrivateMessageParams
    echo: str


class GroupMessageParams(BaseModel):
    group_id: int
    message: list[MessageSegment]


class GroupMessagePayload(BaseModel):
    action: Literal["send_group_msg"] = "send_group_msg"
    params: GroupMessageParams
    echo: str


class PokeParams(BaseModel):
    user_id: int
    group_id: int | None = None  # 不填则为私聊戳
    target_id: int | None = None  # 戳一戳对象


class SendPokePayload(BaseModel):
    """发送戳一戳"""

    action: Literal["send_poke"] = "send_poke"
    params: PokeParams


class DeleteMsgParams(BaseModel):
    message_id: int


class DeleteMsgPayload(BaseModel):
    """撤回消息"""

    action: Literal["delete_msg"] = "delete_msg"
    params: DeleteMsgParams


class ForwardMsgParams(BaseModel):
    message_id: int


class ForwardMsgPayload(BaseModel):
    """获取合并转发消息"""

    action: Literal["get_forward_msg"] = "get_forward_msg"
    params: ForwardMsgParams
    echo: str


class EmojiParams(BaseModel):
    message_id: int
    emoji_id: int
    set: bool = True


class SendEmojiPayload(BaseModel):
    """贴表情"""

    action: Literal["set_msg_emoji_like"] = "set_msg_emoji_like"
    params: EmojiParams


class GroupAiRecordParams(BaseModel):
    group_id: int
    character: str
    text: str


class GroupAiRecordPayload(BaseModel):
    """发送群AI语音"""

    action: Literal["send_group_ai_record"] = "send_group_ai_record"
    params: GroupAiRecordParams


class GroupDetailInfoParams(BaseModel):
    group_id: int


class GroupDetailInfoPayload(BaseModel):
    """获取群信息"""

    action: Literal["get_group_detail_info"] = "get_group_detail_info"
    params: GroupDetailInfoParams
    echo: str


class GroupKickParams(BaseModel):
    group_id: int
    user_id: int
    reject_add_request: bool = False


class GroupKickPayload(BaseModel):
    """踢出群成员"""

    action: Literal["set_group_kick"] = "set_group_kick"
    params: GroupKickParams


class GroupBanParameters(BaseModel):
    group_id: int
    user_id: int
    duration: int


class GroupBanPayload(BaseModel):
    """群禁言"""

    action: Literal["set_group_ban"] = "set_group_ban"
    params: GroupBanParameters


# ==================== 获取群精华消息 ====================
class GetEssenceMsgListParams(BaseModel):
    group_id: int


class GetEssenceMsgListPayload(BaseModel):
    """获取群精华消息"""

    action: Literal["get_essence_msg_list"] = "get_essence_msg_list"
    params: GetEssenceMsgListParams
    echo: str


# ==================== 全体禁言 ====================
class SetGroupWholeBanParams(BaseModel):
    group_id: int
    enable: bool


class SetGroupWholeBanPayload(BaseModel):
    """全体禁言"""

    action: Literal["set_group_whole_ban"] = "set_group_whole_ban"
    params: SetGroupWholeBanParams


# ==================== 设置群头像 ====================
class SetGroupPortraitParams(BaseModel):
    group_id: int
    file: str


class SetGroupPortraitPayload(BaseModel):
    """设置群头像"""

    action: Literal["set_group_portrait"] = "set_group_portrait"
    params: SetGroupPortraitParams


# ==================== 设置群管理 ====================
class SetGroupAdminParams(BaseModel):
    group_id: int
    user_id: int
    enable: bool


class SetGroupAdminPayload(BaseModel):
    """设置群管理"""

    action: Literal["set_group_admin"] = "set_group_admin"
    params: SetGroupAdminParams


# ==================== 设置群成员名片 ====================
class SetGroupCardParams(BaseModel):
    group_id: int
    user_id: int
    card: str | None = None  # 为空则为取消群名片


class SetGroupCardPayload(BaseModel):
    """设置群成员名片"""

    action: Literal["set_group_card"] = "set_group_card"
    params: SetGroupCardParams


# ==================== 设置群精华消息 ====================
class SetEssenceMsgParams(BaseModel):
    message_id: int


class SetEssenceMsgPayload(BaseModel):
    """设置群精华消息"""

    action: Literal["set_essence_msg"] = "set_essence_msg"
    params: SetEssenceMsgParams


# ==================== 设置群名 ====================
class SetGroupNameParams(BaseModel):
    group_id: int
    group_name: str


class SetGroupNamePayload(BaseModel):
    """设置群名"""

    action: Literal["set_group_name"] = "set_group_name"
    params: SetGroupNameParams


# ==================== 删除群精华消息 ====================
class DeleteEssenceMsgParams(BaseModel):
    message_id: int


class DeleteEssenceMsgPayload(BaseModel):
    """删除群精华消息"""

    action: Literal["delete_essence_msg"] = "delete_essence_msg"
    params: DeleteEssenceMsgParams


# ==================== _删除群公告 ====================
class DelGroupNoticeParams(BaseModel):
    group_id: int
    notice_id: str


class DelGroupNoticePayload(BaseModel):
    """删除群公告"""

    action: Literal["_del_group_notice"] = "_del_group_notice"
    params: DelGroupNoticeParams


# ==================== 退群 ====================
class SetGroupLeaveParams(BaseModel):
    group_id: int
    is_dismiss: bool | None = None  # 暂无作用


class SetGroupLeavePayload(BaseModel):
    """退群"""

    action: Literal["set_group_leave"] = "set_group_leave"
    params: SetGroupLeaveParams


# ==================== _发送群公告 ====================
class SendGroupNoticeParams(BaseModel):
    group_id: int
    content: str  # 内容
    image: str | None = None  # 图片路径
    pinned: int | None = None
    type: int | None = None
    confirm_required: int | None = None
    is_show_edit_card: int | None = None
    tip_window_type: int | None = None


class SendGroupNoticePayload(BaseModel):
    """发送群公告"""

    action: Literal["_send_group_notice"] = "_send_group_notice"
    params: SendGroupNoticeParams


# ==================== 设置群搜索 ====================
class SetGroupSearchParams(BaseModel):
    group_id: int
    no_code_finger_open: int | None = None
    no_finger_open: int | None = None


class SetGroupSearchPayload(BaseModel):
    """设置群搜索"""

    action: Literal["set_group_search"] = "set_group_search"
    params: SetGroupSearchParams


# ==================== _获取群公告 ====================
class GetGroupNoticeParams(BaseModel):
    group_id: int


class GetGroupNoticePayload(BaseModel):
    """获取群公告"""

    action: Literal["_get_group_notice"] = "_get_group_notice"
    params: GetGroupNoticeParams
    echo: str


# ==================== 处理加群请求 ====================
class SetGroupAddRequestParams(BaseModel):
    flag: str  # 请求id
    approve: bool  # 是否同意
    reason: str | None = None  # 拒绝理由


class SetGroupAddRequestPayload(BaseModel):
    """处理加群请求"""

    action: Literal["set_group_add_request"] = "set_group_add_request"
    params: SetGroupAddRequestParams


# ==================== 获取群信息 ====================
class GetGroupInfoParams(BaseModel):
    group_id: int


class GetGroupInfoPayload(BaseModel):
    """获取群信息"""

    action: Literal["get_group_info"] = "get_group_info"
    params: GetGroupInfoParams
    echo: str


# ==================== 获取群列表 ====================
class GetGroupListParams(BaseModel):
    no_cache: bool = False


class GetGroupListPayload(BaseModel):
    """获取群列表"""

    action: Literal["get_group_list"] = "get_group_list"
    params: GetGroupListParams
    echo: str


# ==================== 获取群成员信息 ====================
class GetGroupMemberInfoParams(BaseModel):
    group_id: int
    user_id: int
    no_cache: bool = False


class GetGroupMemberInfoPayload(BaseModel):
    """获取群成员信息"""

    action: Literal["get_group_member_info"] = "get_group_member_info"
    params: GetGroupMemberInfoParams
    echo: str


# ==================== 获取群成员列表 ====================
class GetGroupMemberListParams(BaseModel):
    group_id: int
    no_cache: bool = False


class GetGroupMemberListPayload(BaseModel):
    """获取群成员列表"""

    action: Literal["get_group_member_list"] = "get_group_member_list"
    params: GetGroupMemberListParams
    echo: str


# ==================== 获取群荣誉 ====================
class GetGroupHonorInfoParams(BaseModel):
    group_id: int
    type: str = "all"  # all, talkative, performer, legend, strong_newbie, emotion


class GetGroupHonorInfoPayload(BaseModel):
    """获取群荣誉"""

    action: Literal["get_group_honor_info"] = "get_group_honor_info"
    params: GetGroupHonorInfoParams
    echo: str


# ==================== 获取群@全体成员剩余次数 ====================
class GetGroupAtAllRemainParams(BaseModel):
    group_id: int


class GetGroupAtAllRemainPayload(BaseModel):
    """获取群@全体成员剩余次数"""

    action: Literal["get_group_at_all_remain"] = "get_group_at_all_remain"
    params: GetGroupAtAllRemainParams
    echo: str


# ==================== 获取群禁言列表 ====================
class GetGroupShutListParams(BaseModel):
    group_id: int


class GetGroupShutListPayload(BaseModel):
    """获取群禁言列表"""

    action: Literal["get_group_shut_list"] = "get_group_shut_list"
    params: GetGroupShutListParams
    echo: str


# ==================== 群打卡 ====================
class SetGroupSignParams(BaseModel):
    group_id: str


class SetGroupSignPayload(BaseModel):
    """群打卡"""

    action: Literal["set_group_sign"] = "set_group_sign"
    params: SetGroupSignParams


# ==================== 设置群代办 ====================
class SetGroupTodoParams(BaseModel):
    group_id: str
    message_id: str
    message_seq: str | None = None


class SetGroupTodoPayload(BaseModel):
    """设置群代办"""

    action: Literal["set_group_todo"] = "set_group_todo"
    params: SetGroupTodoParams


# ==================== 上传群文件 ====================
class UploadGroupFileParams(BaseModel):
    group_id: int | str
    file: str  # 文件路径
    name: str  # 文件名
    folder: str | None = None  # 文件夹ID（二选一）
    folder_id: str | None = None  # 文件夹ID（二选一）


class UploadGroupFilePayload(BaseModel):
    """上传群文件"""

    action: Literal["upload_group_file"] = "upload_group_file"
    params: UploadGroupFileParams


# ==================== 上传私聊文件 ====================
class UploadPrivateFileParams(BaseModel):
    user_id: int | str
    file: str  # 文件路径
    name: str  # 文件名


class UploadPrivateFilePayload(BaseModel):
    """上传私聊文件"""

    action: Literal["upload_private_file"] = "upload_private_file"
    params: UploadPrivateFileParams


# ==================== 获取群根目录文件列表 ====================
class GetGroupRootFilesParams(BaseModel):
    group_id: int | str
    file_count: int = 50  # 一次性获取的文件数量


class GetGroupRootFilesPayload(BaseModel):
    """获取群根目录文件列表"""

    action: Literal["get_group_root_files"] = "get_group_root_files"
    params: GetGroupRootFilesParams
    echo: str


# ==================== 获取群子目录文件列表 ====================
class GetGroupFilesByFolderParams(BaseModel):
    group_id: int | str
    folder_id: str | None = None  # 和folder二选一
    folder: str | None = None  # 和folder_id二选一
    file_count: int = 50  # 一次性获取的文件数量


class GetGroupFilesByFolderPayload(BaseModel):
    """获取群子目录文件列表"""

    action: Literal["get_group_files_by_folder"] = "get_group_files_by_folder"
    params: GetGroupFilesByFolderParams
    echo: str


# ==================== 获取群文件系统信息 ====================
class GetGroupFileSystemInfoParams(BaseModel):
    group_id: int | str


class GetGroupFileSystemInfoPayload(BaseModel):
    """获取群文件系统信息"""

    action: Literal["get_group_file_system_info"] = "get_group_file_system_info"
    params: GetGroupFileSystemInfoParams
    echo: str


# ==================== 获取文件信息 ====================
class GetFileParams(BaseModel):
    file_id: str | None = None  # 二选一
    file: str | None = None  # 二选一


class GetFilePayload(BaseModel):
    """获取文件信息"""

    action: Literal["get_file"] = "get_file"
    params: GetFileParams
    echo: str


# ==================== 获取群文件链接 ====================
class GetGroupFileUrlParams(BaseModel):
    group_id: int | str
    file_id: str


class GetGroupFileUrlPayload(BaseModel):
    """获取群文件链接"""

    action: Literal["get_group_file_url"] = "get_group_file_url"
    params: GetGroupFileUrlParams
    echo: str


# ==================== 获取私聊文件链接 ====================
class GetPrivateFileUrlParams(BaseModel):
    file_id: str


class GetPrivateFileUrlPayload(BaseModel):
    """获取私聊文件链接"""

    action: Literal["get_private_file_url"] = "get_private_file_url"
    params: GetPrivateFileUrlParams
    echo: str


# ==================== 创建群文件文件夹 ====================
class CreateGroupFileFolderParams(BaseModel):
    group_id: int | str
    folder_name: str


class CreateGroupFileFolderPayload(BaseModel):
    """创建群文件文件夹"""

    action: Literal["create_group_file_folder"] = "create_group_file_folder"
    params: CreateGroupFileFolderParams
    echo: str


# ==================== 删除群文件 ====================
class DeleteGroupFileParams(BaseModel):
    group_id: int | str
    file_id: str


class DeleteGroupFilePayload(BaseModel):
    """删除群文件"""

    action: Literal["delete_group_file"] = "delete_group_file"
    params: DeleteGroupFileParams
    echo: str


# ==================== 删除群文件夹 ====================
class DeleteGroupFolderParams(BaseModel):
    group_id: int | str
    folder_id: str


class DeleteGroupFolderPayload(BaseModel):
    """删除群文件夹"""

    action: Literal["delete_group_folder"] = "delete_group_folder"
    params: DeleteGroupFolderParams
    echo: str


# ==================== 移动群文件 ====================
class MoveGroupFileParams(BaseModel):
    group_id: int | str
    file_id: str
    current_parent_directory: str  # 当前父目录，根目录填 /
    target_parent_directory: str  # 目标父目录


class MoveGroupFilePayload(BaseModel):
    """移动群文件"""

    action: Literal["move_group_file"] = "move_group_file"
    params: MoveGroupFileParams
    echo: str


# ==================== 重命名群文件 ====================
class RenameGroupFileParams(BaseModel):
    group_id: int | str
    file_id: str
    current_parent_directory: str  # 当前父目录
    new_name: str  # 新文件名


class RenameGroupFilePayload(BaseModel):
    """重命名群文件"""

    action: Literal["rename_group_file"] = "rename_group_file"
    params: RenameGroupFileParams
    echo: str


# ==================== 删除群相册文件 ====================
class DelGroupAlbumMediaParams(BaseModel):
    group_id: str
    album_id: str
    lloc: str


class DelGroupAlbumMediaPayload(BaseModel):
    """删除群相册文件"""

    action: Literal["del_group_album_media"] = "del_group_album_media"
    params: DelGroupAlbumMediaParams


# ==================== 点赞群相册 ====================
class SetGroupAlbumMediaLikeParams(BaseModel):
    group_id: str
    album_id: str
    lloc: str
    id: str
    set: bool = True


class SetGroupAlbumMediaLikePayload(BaseModel):
    """点赞群相册"""

    action: Literal["set_group_album_media_like"] = "set_group_album_media_like"
    params: SetGroupAlbumMediaLikeParams


# ==================== 查看群相册评论 ====================
class DoGroupAlbumCommentParams(BaseModel):
    group_id: str
    album_id: str
    lloc: str
    content: str


class DoGroupAlbumCommentPayload(BaseModel):
    """查看群相册评论"""

    action: Literal["do_group_album_comment"] = "do_group_album_comment"
    params: DoGroupAlbumCommentParams


# ==================== 获取群相册列表 ====================
class GetGroupAlbumMediaListParams(BaseModel):
    group_id: str
    album_id: str
    attach_info: str


class GetGroupAlbumMediaListPayload(BaseModel):
    """获取群相册列表"""

    action: Literal["get_group_album_media_list"] = "get_group_album_media_list"
    params: GetGroupAlbumMediaListParams
    echo: str


# ==================== 上传图片到群相册 ====================
class UploadImageToQunAlbumParams(BaseModel):
    group_id: str
    album_id: str
    album_name: str
    file: str


class UploadImageToQunAlbumPayload(BaseModel):
    """上传图片到群相册"""

    action: Literal["upload_image_to_qun_album"] = "upload_image_to_qun_album"
    params: UploadImageToQunAlbumParams


# ==================== 获取群相册总列表 ====================
class GetQunAlbumListParams(BaseModel):
    group_id: str


class GetQunAlbumListPayload(BaseModel):
    """获取群相册总列表"""

    action: Literal["get_qun_album_list"] = "get_qun_album_list"
    params: GetQunAlbumListParams
    echo: str


class AiCharactersParams(BaseModel):
    group_id: int
    chat_type: Literal[1, 2] = 1


class AiCharactersPayload(BaseModel):
    """获取群AI角色列表"""

    action: Literal["get_ai_characters"] = "get_ai_characters"
    params: AiCharactersParams
    echo: str
