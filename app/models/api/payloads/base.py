"""基础 Payload 模型"""

from typing import Literal

from pydantic import BaseModel

from ...segments import MessageSegment


# ==================== 消息发送相关 ====================
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


# ==================== 戳一戳 ====================
class PokeParams(BaseModel):
    user_id: int
    group_id: int | None = None
    target_id: int | None = None


class SendPokePayload(BaseModel):
    """发送戳一戳"""

    action: Literal["send_poke"] = "send_poke"
    params: PokeParams


# ==================== 撤回消息 ====================
class DeleteMsgParams(BaseModel):
    message_id: int


class DeleteMsgPayload(BaseModel):
    """撤回消息"""

    action: Literal["delete_msg"] = "delete_msg"
    params: DeleteMsgParams


# ==================== 获取合并转发消息 ====================
class ForwardMsgParams(BaseModel):
    message_id: int


class ForwardMsgPayload(BaseModel):
    """获取合并转发消息"""

    action: Literal["get_forward_msg"] = "get_forward_msg"
    params: ForwardMsgParams
    echo: str


# ==================== 贴表情 ====================
class EmojiParams(BaseModel):
    message_id: int
    emoji_id: int
    set: bool = True


class SendEmojiPayload(BaseModel):
    """贴表情"""

    action: Literal["set_msg_emoji_like"] = "set_msg_emoji_like"
    params: EmojiParams


# ==================== 获取消息详情 ====================
class GetMsgParams(BaseModel):
    message_id: int


class GetMsgPayload(BaseModel):
    """获取消息详情"""

    action: Literal["get_msg"] = "get_msg"
    params: GetMsgParams
    echo: str


# ==================== 获取群历史消息 ====================
class GetGroupMsgHistoryParams(BaseModel):
    group_id: int
    message_seq: int | None = None
    count: int = 20
    reverseOrder: bool = False


class GetGroupMsgHistoryPayload(BaseModel):
    """获取群历史消息"""

    action: Literal["get_group_msg_history"] = "get_group_msg_history"
    params: GetGroupMsgHistoryParams
    echo: str


# ==================== 获取好友历史消息 ====================
class GetFriendMsgHistoryParams(BaseModel):
    user_id: int
    message_seq: int | None = None
    count: int = 20
    reverseOrder: bool = False


class GetFriendMsgHistoryPayload(BaseModel):
    """获取好友历史消息"""

    action: Literal["get_friend_msg_history"] = "get_friend_msg_history"
    params: GetFriendMsgHistoryParams
    echo: str


# ==================== 获取贴表情详情 ====================
class FetchEmojiLikeParams(BaseModel):
    message_id: int
    emojiId: str
    emojiType: str
    count: int | None = None


class FetchEmojiLikePayload(BaseModel):
    """获取贴表情详情"""

    action: Literal["fetch_emoji_like"] = "fetch_emoji_like"
    params: FetchEmojiLikeParams
    echo: str


# ==================== 获取语音消息详情 ====================
class GetRecordParams(BaseModel):
    file: str | None = None
    file_id: str | None = None
    out_format: str = "mp3"


class GetRecordPayload(BaseModel):
    """获取语音消息详情"""

    action: Literal["get_record"] = "get_record"
    params: GetRecordParams
    echo: str


# ==================== 获取图片消息详情 ====================
class GetImageParams(BaseModel):
    file_id: str | None = None
    file: str | None = None


class GetImagePayload(BaseModel):
    """获取图片消息详情"""

    action: Literal["get_image"] = "get_image"
    params: GetImageParams
    echo: str


# ==================== 消息转发到群 ====================
class ForwardGroupSingleMsgParams(BaseModel):
    group_id: int
    message_id: int


class ForwardGroupSingleMsgPayload(BaseModel):
    """消息转发到群"""

    action: Literal["forward_group_single_msg"] = "forward_group_single_msg"
    params: ForwardGroupSingleMsgParams


# ==================== 消息转发到私聊 ====================
class ForwardFriendSingleMsgParams(BaseModel):
    user_id: int
    message_id: int


class ForwardFriendSingleMsgPayload(BaseModel):
    """消息转发到私聊"""

    action: Literal["forward_friend_single_msg"] = "forward_friend_single_msg"
    params: ForwardFriendSingleMsgParams


# ==================== 群聊戳一戳 ====================
class GroupPokeParams(BaseModel):
    group_id: int
    user_id: int


class GroupPokePayload(BaseModel):
    """发送群聊戳一戳"""

    action: Literal["group_poke"] = "group_poke"
    params: GroupPokeParams


# ==================== 私聊戳一戳 ====================
class FriendPokeParams(BaseModel):
    user_id: int


class FriendPokePayload(BaseModel):
    """发送私聊戳一戳"""

    action: Literal["friend_poke"] = "friend_poke"
    params: FriendPokeParams
