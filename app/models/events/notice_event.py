from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Notice(BaseModel):
    """通知事件基类"""

    time: int  # 事件发生的 Unix 时间戳
    self_id: int  # 收到事件的机器人 QQ 号
    post_type: Literal["notice"]


class GroupNoticeEvent(Notice):
    """群通知事件基类"""

    group_id: int  # 群号
    user_id: int  # 触发事件的用户 QQ 号


class GroupRecallNoticeEvent(GroupNoticeEvent):
    """群撤回消息通知事件"""

    notice_type: Literal["group_recall"]
    operator_id: int  # 操作者 QQ 号 (如果是自己撤回则与 user_id 相同)
    message_id: int  # 被撤回的消息 ID


class GroupDecreaseEvent(GroupNoticeEvent):
    """群成员减少通知事件"""

    notice_type: Literal["group_decrease"]
    operator_id: int  # 操作者 QQ 号 (踢人时有效，主动退群时为 user_id)
    sub_type: Literal["leave", "kick", "kick_me", "disband"]
    # leave: 主动退群, kick: 被踢, kick_me: 登录号被踢, disband: 群解散


class GroupAdminNoticeEvent(GroupNoticeEvent):
    """群管理员变更通知事件"""

    notice_type: Literal["group_admin"]
    sub_type: Literal["set", "unset"]
    # set: 设置管理员, unset: 取消管理员


class GroupIncreaseEvent(GroupNoticeEvent):
    """群成员增加通知事件"""

    notice_type: Literal["group_increase"]
    operator_id: int  # 操作者 QQ 号 (即管理员/群主 QQ 号，approve 时为空)
    sub_type: Literal["approve", "invite"]
    # approve: 管理员同意入群, invite: 管理员邀请入群


class GroupBanEvent(GroupNoticeEvent):
    """群成员禁言通知事件"""

    notice_type: Literal["group_ban"]
    operator_id: int  # 操作者 QQ 号
    duration: int  # 禁言时长 (秒)，0 表示解除禁言
    sub_type: Literal["ban", "lift_ban"]
    # ban: 禁言, lift_ban: 解除禁言


class GroupUploadFile(BaseModel):
    """群文件信息"""

    id: str  # 文件 ID
    name: str  # 文件名称
    size: int  # 文件大小 (字节)
    busid: int  # 文件总线 ID (用于下载文件)


class GroupUploadNoticeEvent(GroupNoticeEvent):
    """群文件上传通知事件"""

    notice_type: Literal["group_upload"]
    file: GroupUploadFile  # 文件信息


class GroupCardEvent(GroupNoticeEvent):
    """群成员名片变更通知事件"""

    notice_type: Literal["group_card"]
    card_new: str  # 新名片
    card_old: str  # 旧名片


class GroupNameEvent(GroupNoticeEvent):
    """群名称变更通知事件"""

    notice_type: Literal["notify"]
    sub_type: Literal["group_name"]
    name_new: str  # 新群名


class GroupTitleEvent(GroupNoticeEvent):
    """群头衔变更通知事件"""

    notice_type: Literal["notify"]
    sub_type: Literal["title"]
    title: str  # 新头衔


class GroupEssenceEvent(GroupNoticeEvent):
    """群精华消息变更通知事件"""

    notice_type: Literal["essence"]
    sub_type: Literal["add", "delete"]
    # add: 添加精华, delete: 移除精华
    message_id: int  # 消息 ID
    sender_id: int  # 消息发送者 QQ 号
    operator_id: int  # 操作者 QQ 号 (设置/取消精华的管理员)


class MsgEmojiLike(BaseModel):
    """表情回应信息"""

    emoji_id: str  # 表情 ID
    count: int  # 该表情的回应数量


class GroupMsgEmojiLikeEvent(GroupNoticeEvent):
    """群消息表情回应通知事件 (NapCat 扩展)"""

    notice_type: Literal["group_msg_emoji_like"]
    message_id: int  # 被回应的消息 ID
    likes: list[MsgEmojiLike]  # 表情回应列表
    is_add: bool = True  # 是否是添加表情 (True: 添加, False: 移除)


class GroupPokeEvent(GroupNoticeEvent):
    """群戳一戳通知事件"""

    notice_type: Literal["notify"]
    sub_type: Literal["poke"]
    target_id: int  # 被戳的人 QQ 号
    raw_info: Any = None  # 原始戳一戳信息 (NapCat 扩展, 包含戳一戳动作详情)


class GroupLuckyKingEvent(GroupNoticeEvent):
    """群红包运气王通知事件"""

    notice_type: Literal["notify"]
    sub_type: Literal["lucky_king"]
    target_id: int  # 运气王 QQ 号


class GroupHonorEvent(GroupNoticeEvent):
    """群荣誉变更通知事件"""

    notice_type: Literal["notify"]
    sub_type: Literal["honor"]
    honor_type: Literal["talkative", "performer", "emotion"]
    # talkative: 龙王, performer: 群聊之火, emotion: 快乐源泉


class FriendAddNoticeEvent(Notice):
    """好友添加通知事件 - 新好友已添加"""

    notice_type: Literal["friend_add"]
    user_id: int  # 新好友 QQ 号


class FriendRecallNoticeEvent(Notice):
    """好友撤回消息通知事件"""

    notice_type: Literal["friend_recall"]
    user_id: int  # 好友 QQ 号
    message_id: int  # 被撤回的消息 ID


class FriendPokeEvent(Notice):
    """好友戳一戳通知事件"""

    notice_type: Literal["notify"]
    sub_type: Literal["poke"]
    target_id: int  # 被戳的人 QQ 号
    user_id: int  # 发起戳一戳的人 QQ 号 (与 sender_id 相同)
    sender_id: int  # 发送者 QQ 号
    raw_info: Any = None  # 原始戳一戳信息 (NapCat 扩展)


class ProfileLikeEvent(Notice):
    """资料点赞通知事件 (NapCat 扩展) - 有人给你的资料卡点赞"""

    notice_type: Literal["notify"]
    sub_type: Literal["profile_like"]
    operator_id: int  # 点赞人 QQ 号
    operator_nick: str  # 点赞人昵称
    times: int  # 点赞次数


class InputStatusEvent(Notice):
    """输入状态通知事件 (NapCat 扩展) - 对方正在输入"""

    notice_type: Literal["notify"]
    sub_type: Literal["input_status"]
    status_text: str  # 状态文本，如 "对方正在输入..."
    event_type: int  # 事件类型 (1: 开始输入)
    user_id: int  # 用户 QQ 号
    group_id: int = 0  # 群号 (私聊时为 0)


class BotOfflineEvent(Notice):
    """Bot 离线通知事件 (NapCat 扩展) - Bot 被挤下线等情况"""

    notice_type: Literal["bot_offline"]
    user_id: int  # Bot QQ 号
    tag: str  # 离线标签/类型
    message: str  # 离线原因描述


type PokeEvent = GroupPokeEvent | FriendPokeEvent


type NotifyEvent = Annotated[
    GroupNameEvent
    | GroupTitleEvent
    | PokeEvent
    | ProfileLikeEvent
    | InputStatusEvent
    | GroupLuckyKingEvent
    | GroupHonorEvent,
    Field(discriminator="sub_type"),
]

type NoticeEvent = Annotated[
    NotifyEvent
    | GroupRecallNoticeEvent
    | GroupDecreaseEvent
    | GroupAdminNoticeEvent
    | GroupIncreaseEvent
    | GroupBanEvent
    | GroupUploadNoticeEvent
    | GroupCardEvent
    | GroupEssenceEvent
    | GroupMsgEmojiLikeEvent
    | FriendAddNoticeEvent
    | FriendRecallNoticeEvent
    | BotOfflineEvent,
    Field(discriminator="notice_type"),
]
