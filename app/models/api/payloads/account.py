"""账号相关 Payload 模型"""

from typing import Literal

from pydantic import BaseModel


# ==================== 设置消息已读 ====================
class MarkMsgAsReadParams(BaseModel):
    group_id: int | None = None
    user_id: int | None = None


class MarkMsgAsReadPayload(BaseModel):
    """设置消息已读"""

    action: Literal["mark_msg_as_read"] = "mark_msg_as_read"
    params: MarkMsgAsReadParams


# ==================== 设置私聊已读 ====================
class MarkPrivateMsgAsReadParams(BaseModel):
    user_id: int


class MarkPrivateMsgAsReadPayload(BaseModel):
    """设置私聊已读"""

    action: Literal["mark_private_msg_as_read"] = "mark_private_msg_as_read"
    params: MarkPrivateMsgAsReadParams


# ==================== 设置群聊已读 ====================
class MarkGroupMsgAsReadParams(BaseModel):
    group_id: int


class MarkGroupMsgAsReadPayload(BaseModel):
    """设置群聊已读"""

    action: Literal["mark_group_msg_as_read"] = "mark_group_msg_as_read"
    params: MarkGroupMsgAsReadParams


# ==================== 获取最近消息列表 ====================
class GetRecentContactParams(BaseModel):
    count: int = 10


class GetRecentContactPayload(BaseModel):
    """获取最近消息列表"""

    action: Literal["get_recent_contact"] = "get_recent_contact"
    params: GetRecentContactParams
    echo: str


# ==================== 设置所有消息已读 ====================
class MarkAllAsReadPayload(BaseModel):
    """设置所有消息已读"""

    action: Literal["_mark_all_as_read"] = "_mark_all_as_read"


# ==================== 点赞 ====================
class SendLikeParams(BaseModel):
    user_id: int
    times: int = 1


class SendLikePayload(BaseModel):
    """点赞"""

    action: Literal["send_like"] = "send_like"
    params: SendLikeParams


# ==================== 处理好友请求 ====================
class SetFriendAddRequestParams(BaseModel):
    flag: str
    approve: bool
    remark: str | None = None


class SetFriendAddRequestPayload(BaseModel):
    """处理好友请求"""

    action: Literal["set_friend_add_request"] = "set_friend_add_request"
    params: SetFriendAddRequestParams


# ==================== 获取账号信息 ====================
class GetStrangerInfoParams(BaseModel):
    user_id: int


class GetStrangerInfoPayload(BaseModel):
    """获取账号信息"""

    action: Literal["get_stranger_info"] = "get_stranger_info"
    params: GetStrangerInfoParams
    echo: str


# ==================== 获取好友列表 ====================
class GetFriendListParams(BaseModel):
    no_cache: bool = False


class GetFriendListPayload(BaseModel):
    """获取好友列表"""

    action: Literal["get_friend_list"] = "get_friend_list"
    params: GetFriendListParams
    echo: str


# ==================== 获取好友分组列表 ====================
class GetFriendsWithCategoryPayload(BaseModel):
    """获取好友分组列表"""

    action: Literal["get_friends_with_category"] = "get_friends_with_category"
    echo: str


# ==================== 设置账号信息 ====================
class SetQqProfileParams(BaseModel):
    nickname: str
    personal_note: str | None = None
    sex: str | None = None


class SetQqProfilePayload(BaseModel):
    """设置账号信息"""

    action: Literal["set_qq_profile"] = "set_qq_profile"
    params: SetQqProfileParams


# ==================== 删除好友 ====================
class DeleteFriendParams(BaseModel):
    user_id: int | None = None
    friend_id: int | None = None
    temp_block: bool = False
    temp_both_del: bool = False


class DeleteFriendPayload(BaseModel):
    """删除好友"""

    action: Literal["delete_friend"] = "delete_friend"
    params: DeleteFriendParams


# ==================== 获取推荐好友/群聊卡片 ====================
class ArkSharePeerParams(BaseModel):
    group_id: int | None = None
    user_id: int | None = None
    phoneNumber: str | None = None


class ArkSharePeerPayload(BaseModel):
    """获取推荐好友/群聊卡片"""

    action: Literal["ArkSharePeer"] = "ArkSharePeer"
    params: ArkSharePeerParams
    echo: str


# ==================== 获取推荐群聊卡片 ====================
class ArkShareGroupParams(BaseModel):
    group_id: str


class ArkShareGroupPayload(BaseModel):
    """获取推荐群聊卡片"""

    action: Literal["ArkShareGroup"] = "ArkShareGroup"
    params: ArkShareGroupParams
    echo: str


# ==================== 设置在线状态 ====================
class SetOnlineStatusParams(BaseModel):
    status: int
    ext_status: int
    battery_status: int = 0


class SetOnlineStatusPayload(BaseModel):
    """设置在线状态"""

    action: Literal["set_online_status"] = "set_online_status"
    params: SetOnlineStatusParams


# ==================== 设置自定义在线状态 ====================
class SetDiyOnlineStatusParams(BaseModel):
    face_id: int
    face_type: int | None = None
    wording: str | None = None


class SetDiyOnlineStatusPayload(BaseModel):
    """设置自定义在线状态"""

    action: Literal["set_diy_online_status"] = "set_diy_online_status"
    params: SetDiyOnlineStatusParams


# ==================== 设置头像 ====================
class SetQqAvatarParams(BaseModel):
    file: str


class SetQqAvatarPayload(BaseModel):
    """设置头像"""

    action: Literal["set_qq_avatar"] = "set_qq_avatar"
    params: SetQqAvatarParams


# ==================== 创建收藏 ====================
class CreateCollectionParams(BaseModel):
    rawData: str
    brief: str


class CreateCollectionPayload(BaseModel):
    """创建收藏"""

    action: Literal["create_collection"] = "create_collection"
    params: CreateCollectionParams


# ==================== 设置个性签名 ====================
class SetSelfLongnickParams(BaseModel):
    longNick: str


class SetSelfLongnickPayload(BaseModel):
    """设置个性签名"""

    action: Literal["set_self_longnick"] = "set_self_longnick"
    params: SetSelfLongnickParams


# ==================== 获取收藏表情 ====================
class FetchCustomFaceParams(BaseModel):
    count: int = 40


class FetchCustomFacePayload(BaseModel):
    """获取收藏表情"""

    action: Literal["fetch_custom_face"] = "fetch_custom_face"
    params: FetchCustomFaceParams
    echo: str


# ==================== 获取点赞列表 ====================
class GetProfileLikeParams(BaseModel):
    user_id: int | None = None
    start: int = 0
    count: int = 10


class GetProfileLikePayload(BaseModel):
    """获取点赞列表"""

    action: Literal["get_profile_like"] = "get_profile_like"
    params: GetProfileLikeParams
    echo: str


# ==================== 获取用户状态 ====================
class NcGetUserStatusParams(BaseModel):
    user_id: int


class NcGetUserStatusPayload(BaseModel):
    """获取用户状态"""

    action: Literal["nc_get_user_status"] = "nc_get_user_status"
    params: NcGetUserStatusParams
    echo: str


# ==================== 获取单向好友列表 ====================
class GetUnidirectionalFriendListPayload(BaseModel):
    """获取单向好友列表"""

    action: Literal["get_unidirectional_friend_list"] = "get_unidirectional_friend_list"
    echo: str


# ==================== 获取登录号信息 ====================
class GetLoginInfoPayload(BaseModel):
    """获取登录号信息"""

    action: Literal["get_login_info"] = "get_login_info"
    echo: str


# ==================== 获取状态 ====================
class GetStatusPayload(BaseModel):
    """获取状态"""

    action: Literal["get_status"] = "get_status"
    echo: str


# ==================== 获取在线客户端列表 ====================
class GetOnlineClientsPayload(BaseModel):
    """获取在线客户端列表"""

    action: Literal["get_online_clients"] = "get_online_clients"
    echo: str


# ==================== 获取在线机型 ====================
class GetModelShowParams(BaseModel):
    model: str


class GetModelShowPayload(BaseModel):
    """获取在线机型"""

    action: Literal["_get_model_show"] = "_get_model_show"
    params: GetModelShowParams
    echo: str


# ==================== 设置在线机型 ====================
class SetModelShowParams(BaseModel):
    model: str
    model_show: str


class SetModelShowPayload(BaseModel):
    """设置在线机型"""

    action: Literal["_set_model_show"] = "_set_model_show"
    params: SetModelShowParams


# ==================== 获取被过滤好友请求 ====================
class GetDoubtFriendsAddRequestParams(BaseModel):
    count: int = 50


class GetDoubtFriendsAddRequestPayload(BaseModel):
    """获取被过滤好友请求"""

    action: Literal["get_doubt_friends_add_request"] = "get_doubt_friends_add_request"
    params: GetDoubtFriendsAddRequestParams
    echo: str


# ==================== 处理被过滤好友请求 ====================
class SetDoubtFriendsAddRequestParams(BaseModel):
    flag: str
    approve: bool


class SetDoubtFriendsAddRequestPayload(BaseModel):
    """处理被过滤好友请求"""

    action: Literal["set_doubt_friends_add_request"] = "set_doubt_friends_add_request"
    params: SetDoubtFriendsAddRequestParams


# ==================== 设置好友备注 ====================
class SetFriendRemarkParams(BaseModel):
    user_id: int
    remark: str


class SetFriendRemarkPayload(BaseModel):
    """设置好友备注"""

    action: Literal["set_friend_remark"] = "set_friend_remark"
    params: SetFriendRemarkParams
