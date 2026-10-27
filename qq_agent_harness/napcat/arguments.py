"""明确的 NapCat 工具参数模型，不向模型开放原始 action 或任意参数字典。"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..contracts import Model

Id = Annotated[str, Field(min_length=1, description="QQ/NapCat 原生标识，按字符串填写")]


class Empty(Model):
    pass


class Fresh(Model):
    no_cache: bool = Field(default=False, description="是否刷新远端缓存")


class User(Model):
    user_id: Id = Field(description="操作对象 QQ 号；不是发起者权限")


class UserFresh(User):
    no_cache: bool = Field(default=False, description="是否刷新远端缓存")


class MemberBan(User):
    duration: int = Field(default=60, ge=0, description="禁言秒数，0 为解除禁言")


class MemberKick(User):
    reject_add_request: bool = Field(default=False, description="是否拒绝此成员后续加群申请")


class MembersKick(Model):
    user_id: list[Id] = Field(min_length=1, description="需要移出当前群的 QQ 号列表")
    reject_add_request: bool = Field(default=False, description="是否拒绝这些成员后续加群申请")


class Enabled(Model):
    enable: bool = Field(description="开启或关闭该能力")


class MemberAdmin(User):
    enable: bool = Field(description="设置或取消 QQ 平台的群管理员身份")


class MemberCard(User):
    card: str = Field(description="新群名片，空字符串恢复默认")


class MemberTitle(User):
    special_title: str = Field(description="群专属头衔")


class GroupName(Model):
    group_name: str = Field(min_length=1, description="新的群名称")


class GroupRemark(Model):
    remark: str = Field(description="机器人账号对当前群的备注")


class GroupLeave(Model):
    is_dismiss: bool = Field(default=False, description="机器人是群主时是否解散群；否则仅退出")


class Honor(Model):
    type: Literal["talkative", "performer", "legend", "strong_newbie", "emotion", "all"] = Field(
        default="all", description="荣誉类别"
    )


class MessageId(Model):
    message_id: Id = Field(description="当前会话中的消息 ID，不能填其他会话的 ID")


class Reaction(MessageId):
    emoji_id: Id = Field(description="QQ 表情 ID")
    set: bool = Field(default=True, description="添加或取消表情回应")


class ReactionList(MessageId):
    emoji_id: Id = Field(description="需要查看的表情 ID")
    emoji_type: str = Field(default="1", description="QQ 表情类别")
    count: int = Field(default=100, ge=1, description="最多返回回应者数量")


class EmojiPage(MessageId):
    emojiId: Id = Field(description="表情 ID")
    emojiType: str = Field(default="1", description="表情类型")
    count: int = Field(default=20, ge=1, description="返回数量")
    cookie: str = Field(default="", description="接口返回的分页标记，不是账号登录 Cookie")


class CreateCollection(Model):
    rawData: str = Field(min_length=1, description="要保存到 QQ 收藏的内容")
    brief: str = Field(min_length=1, description="收藏标题")


class DeleteFaces(Model):
    res_id: list[Id] = Field(min_length=1, description="表情详情返回的 resId 列表")


class DoubtRequest(Model):
    flag: Id = Field(description="可疑好友申请标识")
    approve: Literal[True] = Field(default=True, description="此 NapCat 接口仅支持接受申请")


class AlbumLike(Model):
    album_id: Id = Field(description="当前群相册 ID")
    batch_id: Id = Field(description="相册上传批次 ID")
    lloc: str | None = Field(default=None, description="单个媒体 ID；省略时针对整个批次")


class AlbumDelete(Model):
    album_id: Id = Field(description="当前群相册 ID")
    lloc: Id = Field(description="要删除的媒体 ID")


class AlbumComment(AlbumLike):
    content: str = Field(min_length=1, description="相册评论正文")


class History(Model):
    count: int = Field(default=30, ge=1, description="最多获取条数；远端可能只返回部分历史")
    before_message_id: str | None = Field(
        default=None, description="当前会话中用于向前翻页的消息 ID"
    )
    minutes: int | None = Field(
        default=None, ge=1, description="筛选最近多少分钟；仅筛选本次返回的有界历史"
    )
    start_time: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
        description="北京时间起点，严格 YYYY-MM-DD HH:MM:SS",
    )
    end_time: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
        description="北京时间终点，严格 YYYY-MM-DD HH:MM:SS",
    )

    @model_validator(mode="after")
    def validate_range(self) -> History:
        if bool(self.start_time) != bool(self.end_time):
            raise ValueError("start_time 与 end_time 必须同时填写")
        if self.minutes is not None and self.start_time:
            raise ValueError("minutes 与明确时间范围只能选择一种")
        return self


class Forward(MessageId):
    forward_path: list[str] = Field(
        default_factory=list,
        description="嵌套转发路径，使用返回的 forward_paths；省略时读取外层转发",
    )
    offset: int = Field(default=0, ge=0, description="分页起点")
    limit: int = Field(default=100, ge=1, description="本次读取条数；有 next_offset 时可继续")


class ReadMedia(Forward):
    indexes: list[int] | None = Field(
        default=None, description="媒体段的零起点索引；先选取指定索引，不受前页数量限制"
    )
    include_forwards: bool = Field(default=True, description="是否展开当前消息中合并转发的媒体")
    limit: int = Field(default=20, ge=1, description="未指定 indexes 时读取的媒体数量")


class Folder(Model):
    folder_id: Id = Field(description="当前群文件夹 ID")


class FolderCreate(Model):
    name: str = Field(min_length=1, description="新文件夹名称")


class FileId(Model):
    file_id: Id = Field(description="当前群文件 ID")


class GroupFile(Model):
    file_id: Id = Field(description="当前群文件 ID")
    busid: int = Field(default=102, ge=0, description="群文件业务标识，使用文件列表返回的 busid")


class MoveFile(Model):
    file_id: Id = Field(description="当前群文件 ID")
    current_parent_directory: str = Field(default="/", description="当前父目录 ID")
    target_parent_directory: str = Field(description="目标目录 ID")


class RenameFile(Model):
    file_id: Id = Field(description="当前群文件 ID")
    current_parent_directory: str = Field(default="/", description="当前父目录 ID")
    new_name: str = Field(min_length=1, description="新的文件名称")


class GroupNotice(Model):
    content: str = Field(min_length=1, description="公告正文")
    pinned: int = Field(default=0, ge=0, le=1, description="是否置顶，0 或 1")
    type: int = Field(default=1, ge=0, le=1, description="公告类型")


class NoticeId(Model):
    notice_id: Id = Field(description="当前群公告 ID")


class Like(User):
    times: int = Field(default=1, ge=1, description="点赞次数")


class FriendRemark(User):
    remark: str = Field(description="好友备注")


class FriendRequest(Model):
    flag: Id = Field(description="好友申请事件中返回的标识")
    approve: bool = Field(description="同意或拒绝")
    remark: str = Field(default="", description="同意后的备注")


class GroupRequest(Model):
    flag: Id = Field(description="入群申请/邀请事件返回的标识")
    sub_type: Literal["add", "invite"] = Field(default="add", description="申请类型")
    approve: bool = Field(description="同意或拒绝")
    reason: str = Field(default="", description="拒绝原因")


class Count(Model):
    count: int = Field(default=20, ge=1, description="最多返回数量")


class Text(Model):
    text: str = Field(min_length=1, description="待处理文字")


class Words(Model):
    words: list[str] = Field(min_length=1, description="待翻译的英文词句列表")


class LongNick(Model):
    longNick: str = Field(description="机器人账号个性签名")


class OnlineStatus(Model):
    status: int = Field(description="QQ 在线状态编号")
    ext_status: int = Field(default=0, description="扩展状态编号")
    battery_status: int = Field(default=0, ge=0, le=100, description="电量状态")


class DiyStatus(Model):
    face_id: Id = Field(description="状态表情编号")
    face_type: int = Field(default=1, description="表情类别")
    wording: str = Field(description="自定义状态文字")


class InputStatus(Model):
    event_type: int = Field(default=1, ge=0, description="输入状态编号")


class QQProfile(Model):
    nickname: str = Field(min_length=1, description="昵称")
    personal_note: str | None = Field(default=None, description="个人介绍")
    sex: Literal[0, 1, 2] | None = Field(default=None, description="性别，0 未知、1 男、2 女")


class ModelShow(Model):
    model: str = Field(description="设备型号")
    model_show: str = Field(default="", description="展示内容")


class CheckUrl(Model):
    url: str = Field(min_length=1, description="需要由 QQ 判断风险的公网 HTTP/HTTPS URL")


class Collection(Model):
    category: str = Field(default="0", description="收藏分类 ID")
    count: str = Field(
        default="20", pattern="^[1-9][0-9]*$", description="数量，正整数的十进制字符串"
    )


class AICharacters(Model):
    chat_type: int = Field(default=1, ge=1, description="NapCat 语音角色类别，默认 1")


class AIRecord(Model):
    character: str = Field(description="AI 声音角色 ID")
    text: str = Field(min_length=1, description="要生成语音的文字")


class Signed(Model):
    start: int = Field(default=0, ge=0, description="起始位置")
    count: int = Field(default=20, ge=1, description="返回数量")


class AlbumId(Model):
    album_id: Id = Field(description="当前群相册 ID")


class AlbumMedia(AlbumId):
    attach_info: str = Field(default="", description="上次返回的分页标记")


class GroupTodo(MessageId):
    pass


class TextPart(Model):
    type: Literal["text"]
    text: str = Field(min_length=1, description="正文；不会把正文内的 CQ 字符串再次解析为动作")


class AtPart(Model):
    type: Literal["at"]
    qq: Id = Field(description="艾特对象 QQ 号；all 表示全体，必须由群策略允许")


class ReplyPart(Model):
    type: Literal["reply"]
    message_id: Id = Field(description="要引用的当前目标会话消息 ID")


class FacePart(Model):
    type: Literal["face"]
    id: Id = Field(description="QQ 表情 ID")


class PokePart(Model):
    type: Literal["poke"]
    user_id: str | None = Field(default=None, description="群内戳一戳对象；私聊省略表示当前对端")


class AIVoicePart(Model):
    type: Literal["ai_voice"]
    character: Id = Field(description="get_ai_characters 返回的声音角色 ID")
    text: str = Field(min_length=1, description="要通过 QQ AI 语音发送的文字")


class MediaPart(Model):
    type: Literal["image", "record", "video", "file"]
    workspace_path: str | None = Field(default=None, description="共享沙箱内的相对文件路径")
    url: str | None = Field(default=None, description="待发送媒体的公网 HTTP/HTTPS URL")
    name: str | None = Field(default=None, description="发送普通文件时显示的文件名")

    @model_validator(mode="after")
    def one_source(self) -> MediaPart:
        if bool(self.workspace_path) == bool(self.url):
            raise ValueError("媒体必须且只能指定 workspace_path 或 url")
        if self.type == "file" and not self.name:
            raise ValueError("发送文件必须提供 name")
        return self


class ImageSource(Model):
    workspace_path: str | None = Field(default=None, description="共享工作区中的图片相对路径")
    url: str | None = Field(default=None, description="公网 HTTP/HTTPS 图片 URL")

    @model_validator(mode="after")
    def one_source(self) -> ImageSource:
        if bool(self.workspace_path) == bool(self.url):
            raise ValueError("图片必须且只能指定 workspace_path 或 url")
        return self


class ForwardPart(Model):
    type: Literal["forward"]
    message_ids: list[Id] = Field(
        min_length=1, description="当前来源会话中要合并转发的消息 ID，按显示顺序"
    )


SendPart = Annotated[
    TextPart | AtPart | ReplyPart | FacePart | PokePart | AIVoicePart | MediaPart | ForwardPart,
    Field(discriminator="type"),
]


class SendMessage(Model):
    parts: list[SendPart] = Field(
        min_length=1,
        description="有序正文、引用、艾特、媒体、文件或合并转发。文件和转发可能分别产生回执",
    )
