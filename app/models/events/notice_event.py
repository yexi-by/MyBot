"""NapCat 通知事件模型。"""

from typing import Annotated, Literal

from pydantic import Field

from app.models.common import JsonValue, NapCatId, NapCatModel, NapCatStringInteger


class Notice(NapCatModel):
    """通知事件基类。"""

    time: int
    self_id: NapCatId
    post_type: Literal["notice"]


class GroupNoticeEvent(Notice):
    """群通知事件基类。"""

    group_id: NapCatId
    user_id: NapCatId


class GroupRecallNoticeEvent(GroupNoticeEvent):
    """群撤回消息通知事件。"""

    notice_type: Literal["group_recall"]
    operator_id: NapCatId
    message_id: NapCatId


class GroupDecreaseEvent(GroupNoticeEvent):
    """群成员减少通知事件。"""

    notice_type: Literal["group_decrease"]
    operator_id: NapCatId
    sub_type: Literal["leave", "kick", "kick_me", "disband"] | str


class GroupAdminNoticeEvent(GroupNoticeEvent):
    """群管理员变更通知事件。"""

    notice_type: Literal["group_admin"]
    sub_type: Literal["set", "unset"] | str


class GroupIncreaseEvent(GroupNoticeEvent):
    """群成员增加通知事件。"""

    notice_type: Literal["group_increase"]
    operator_id: NapCatId | None = None
    sub_type: Literal["approve", "invite"] | str


class GroupBanEvent(GroupNoticeEvent):
    """群成员禁言通知事件。"""

    notice_type: Literal["group_ban"]
    operator_id: NapCatId
    duration: int
    sub_type: Literal["ban", "lift_ban"] | str


class GroupUploadFile(NapCatModel):
    """群文件上传通知中的文件信息。"""

    id: str
    name: str
    size: int
    busid: NapCatStringInteger | None = None


class GroupUploadNoticeEvent(GroupNoticeEvent):
    """群文件上传通知事件。"""

    notice_type: Literal["group_upload"]
    file: GroupUploadFile


class GroupCardEvent(GroupNoticeEvent):
    """群成员名片变更通知事件。"""

    notice_type: Literal["group_card"]
    card_new: str = ""
    card_old: str = ""


class GroupEssenceEvent(GroupNoticeEvent):
    """群精华消息变更通知事件。"""

    notice_type: Literal["essence"]
    sub_type: Literal["add", "delete"] | str
    message_id: NapCatId
    sender_id: NapCatId
    operator_id: NapCatId


class MsgEmojiLike(NapCatModel):
    """消息表情回应信息。"""

    emoji_id: str
    count: int


class GroupMsgEmojiLikeEvent(GroupNoticeEvent):
    """群消息表情回应通知事件。"""

    notice_type: Literal["group_msg_emoji_like"]
    message_id: NapCatId
    likes: list[MsgEmojiLike]
    is_add: bool = True


class FriendAddNoticeEvent(Notice):
    """好友添加通知事件。"""

    notice_type: Literal["friend_add"]
    user_id: NapCatId


class FriendRecallNoticeEvent(Notice):
    """好友撤回消息通知事件。"""

    notice_type: Literal["friend_recall"]
    user_id: NapCatId
    message_id: NapCatId


class BotOfflineEvent(Notice):
    """Bot 离线通知事件。"""

    notice_type: Literal["bot_offline"]
    user_id: NapCatId
    tag: str = ""
    message: str = ""


class NotifyBaseEvent(Notice):
    """notify 通知事件公共字段。"""

    notice_type: Literal["notify"]


class NotifyEvent(NotifyBaseEvent):
    """通用 notify 通知事件。"""

    sub_type: str
    group_id: NapCatId | None = None
    user_id: NapCatId | None = None
    target_id: NapCatId | None = None
    sender_id: NapCatId | None = None
    operator_id: NapCatId | None = None
    operator_nick: str | None = None
    times: int | None = None
    status_text: str | None = None
    event_type: int | None = None
    raw_info: JsonValue = None
    name_new: str | None = None
    title: str | None = None
    honor_type: str | None = None


class GroupNotifyEvent(NotifyBaseEvent):
    """群 notify 通知事件公共字段。"""

    group_id: NapCatId
    user_id: NapCatId


class GroupPokeEvent(GroupNotifyEvent):
    """群戳一戳通知事件。"""

    sub_type: Literal["poke"]
    target_id: NapCatId
    raw_info: JsonValue = None


class FriendPokeEvent(NotifyBaseEvent):
    """好友戳一戳通知事件。"""

    sub_type: Literal["poke"]
    user_id: NapCatId
    target_id: NapCatId
    sender_id: NapCatId | None = None
    raw_info: JsonValue = None


class GroupNameEvent(GroupNotifyEvent):
    """群名称变更通知事件。"""

    sub_type: Literal["group_name"]
    name_new: str


class GroupTitleEvent(GroupNotifyEvent):
    """群头衔变更通知事件。"""

    sub_type: Literal["title"]
    title: str


class GroupLuckyKingEvent(GroupNotifyEvent):
    """群红包运气王通知事件。"""

    sub_type: Literal["lucky_king"]
    target_id: NapCatId


class GroupHonorEvent(GroupNotifyEvent):
    """群荣誉变更通知事件。"""

    sub_type: Literal["honor"]
    honor_type: Literal["talkative", "performer", "emotion"] | str


class ProfileLikeEvent(NotifyBaseEvent):
    """资料点赞通知事件。"""

    sub_type: Literal["profile_like"]
    operator_id: NapCatId
    operator_nick: str = ""
    times: int


class InputStatusEvent(NotifyBaseEvent):
    """输入状态通知事件。"""

    sub_type: Literal["input_status"]
    user_id: NapCatId
    group_id: NapCatId | None = None
    status_text: str = ""
    event_type: int


type PokeEvent = GroupPokeEvent | FriendPokeEvent

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
