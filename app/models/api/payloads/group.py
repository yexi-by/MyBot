"""群聊相关 Payload 模型"""

from typing import Literal

from pydantic import BaseModel


# ==================== 发送群AI语音 ====================
class GroupAiRecordParams(BaseModel):
    group_id: int
    character: str
    text: str


class GroupAiRecordPayload(BaseModel):
    """发送群AI语音"""

    action: Literal["send_group_ai_record"] = "send_group_ai_record"
    params: GroupAiRecordParams


# ==================== 获取群信息 ====================
class GroupDetailInfoParams(BaseModel):
    group_id: int


class GroupDetailInfoPayload(BaseModel):
    """获取群详细信息"""

    action: Literal["get_group_detail_info"] = "get_group_detail_info"
    params: GroupDetailInfoParams
    echo: str


# ==================== 踢出群成员 ====================
class GroupKickParams(BaseModel):
    group_id: int
    user_id: int
    reject_add_request: bool = False


class GroupKickPayload(BaseModel):
    """踢出群成员"""

    action: Literal["set_group_kick"] = "set_group_kick"
    params: GroupKickParams


# ==================== 群禁言 ====================
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
    card: str | None = None


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


# ==================== 删除群公告 ====================
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
    is_dismiss: bool | None = None


class SetGroupLeavePayload(BaseModel):
    """退群"""

    action: Literal["set_group_leave"] = "set_group_leave"
    params: SetGroupLeaveParams


# ==================== 发送群公告 ====================
class SendGroupNoticeParams(BaseModel):
    group_id: int
    content: str
    image: str | None = None
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


# ==================== 获取群公告 ====================
class GetGroupNoticeParams(BaseModel):
    group_id: int


class GetGroupNoticePayload(BaseModel):
    """获取群公告"""

    action: Literal["_get_group_notice"] = "_get_group_notice"
    params: GetGroupNoticeParams
    echo: str


# ==================== 处理加群请求 ====================
class SetGroupAddRequestParams(BaseModel):
    flag: str
    approve: bool
    reason: str | None = None


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
    type: Literal[
        "all", "talkative", "performer", "legend", "strong_newbie", "emotion"
    ] = "all"


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
    group_id: int


class SetGroupSignPayload(BaseModel):
    """群打卡"""

    action: Literal["set_group_sign"] = "set_group_sign"
    params: SetGroupSignParams


# ==================== 设置群代办 ====================
class SetGroupTodoParams(BaseModel):
    group_id: int
    message_id: int
    message_seq: str | None = None


class SetGroupTodoPayload(BaseModel):
    """设置群代办"""

    action: Literal["set_group_todo"] = "set_group_todo"
    params: SetGroupTodoParams


# ==================== 获取群AI角色列表 ====================
class AiCharactersParams(BaseModel):
    group_id: int
    chat_type: Literal[1, 2] = 1


class AiCharactersPayload(BaseModel):
    """获取群AI角色列表"""

    action: Literal["get_ai_characters"] = "get_ai_characters"
    params: AiCharactersParams
    echo: str


# ==================== 设置群头衔 ====================
class SetGroupSpecialTitleParams(BaseModel):
    group_id: int
    user_id: int
    special_title: str | None = None
    duration: int = -1


class SetGroupSpecialTitlePayload(BaseModel):
    """设置群头衔"""

    action: Literal["set_group_special_title"] = "set_group_special_title"
    params: SetGroupSpecialTitleParams


# ==================== 获取群系统消息 ====================
class GetGroupSystemMsgPayload(BaseModel):
    """获取群系统消息"""

    action: Literal["get_group_system_msg"] = "get_group_system_msg"
    echo: str


# ==================== 设置群备注 ====================
class SetGroupRemarkParams(BaseModel):
    group_id: int
    remark: str


class SetGroupRemarkPayload(BaseModel):
    """设置群备注"""

    action: Literal["set_group_remark"] = "set_group_remark"
    params: SetGroupRemarkParams


# ==================== 获取群信息ex ====================
class GetGroupInfoExParams(BaseModel):
    group_id: int


class GetGroupInfoExPayload(BaseModel):
    """获取群信息ex"""

    action: Literal["get_group_info_ex"] = "get_group_info_ex"
    params: GetGroupInfoExParams
    echo: str


# ==================== 获取群过滤系统消息 ====================
class GetGroupIgnoredNotifiesParams(BaseModel):
    group_id: int


class GetGroupIgnoredNotifiesPayload(BaseModel):
    """获取群过滤系统消息"""

    action: Literal["get_group_ignored_notifies"] = "get_group_ignored_notifies"
    params: GetGroupIgnoredNotifiesParams
    echo: str


# ==================== 批量踢出群成员 ====================
class SetGroupKickMembersParams(BaseModel):
    group_id: int
    user_ids: list[int]
    reject_add_request: bool = False


class SetGroupKickMembersPayload(BaseModel):
    """批量踢出群成员"""

    action: Literal["set_group_kick_members"] = "set_group_kick_members"
    params: SetGroupKickMembersParams
