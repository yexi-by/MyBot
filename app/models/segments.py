"""NapCat OneBot 消息段模型。"""

from typing import Annotated, ClassVar, Literal, Self, cast

from pydantic import BaseModel, Field

from .common import JsonObject, JsonValue, NapCatId, StrictModel


class BaseSegment[T](StrictModel):
    """消息段通用基类。"""

    data: T
    _arg_key: ClassVar[str | None] = None

    @classmethod
    def new(cls, arg: object | None = None, **kwargs: object) -> Self:
        """按消息段的主参数构造实例。"""
        data_annotation: object = cls.model_fields["data"].annotation
        if isinstance(data_annotation, type) and issubclass(data_annotation, BaseModel):
            data_kwargs = dict(kwargs)
            if arg is not None:
                if cls._arg_key is None:
                    raise ValueError(f"{cls.__name__} 不支持位置参数构造")
                data_kwargs[cls._arg_key] = arg
            data = data_annotation(**data_kwargs)
            return cls(data=cast(T, data))
        if kwargs:
            raise ValueError(f"{cls.__name__} 不支持关键字参数构造")
        return cls(data=cast(T, arg))


class TextData(StrictModel):
    """文本消息数据。"""

    text: str


class AtData(StrictModel):
    """艾特消息数据。"""

    qq: NapCatId | Literal["all"]
    name: str | None = None


class ImageData(StrictModel):
    """图片消息数据。"""

    file: str
    url: str | None = None
    path: str | None = None
    file_id: str | None = None
    file_size: int | None = None
    summary: str | None = None
    sub_type: str | None = None


class FaceData(StrictModel):
    """系统表情消息数据。"""

    id: NapCatId
    raw: JsonValue = None
    resultId: str | None = None
    chainCount: int | None = None


class ReplyData(StrictModel):
    """回复消息数据。"""

    id: NapCatId


class RecordData(StrictModel):
    """语音消息数据。"""

    file: str
    url: str | None = None
    path: str | None = None
    file_id: str | None = None
    file_size: int | None = None
    magic: bool | None = None


class VideoData(StrictModel):
    """视频消息数据。"""

    file: str
    url: str | None = None
    path: str | None = None
    file_id: str | None = None
    file_size: int | None = None


class FileData(StrictModel):
    """文件消息数据。"""

    file: str
    name: str | None = None
    file_id: str | None = None
    file_size: int | None = None


class JsonData(StrictModel):
    """JSON 消息数据。"""

    data: str | JsonObject


class ForwardData(StrictModel):
    """合并转发消息数据。"""

    id: str
    content: JsonValue = None


class NodeData(StrictModel):
    """合并转发节点消息数据。"""

    id: NapCatId | None = None
    user_id: NapCatId | None = None
    nickname: str | None = None
    content: list["MessageSegment"] | str | None = None


class MusicData(StrictModel):
    """音乐消息数据。"""

    type: str
    id: str | None = None
    url: str | None = None
    audio: str | None = None
    title: str | None = None
    content: str | None = None
    image: str | None = None


class MFaceData(StrictModel):
    """商城表情消息数据。"""

    emoji_id: str
    emoji_package_id: str | None = None
    key: str | None = None
    summary: str | None = None
    url: str | None = None


class MarkdownData(StrictModel):
    """Markdown 消息数据。"""

    content: str


class ContactData(StrictModel):
    """推荐联系人或群聊消息数据。"""

    type: Literal["qq", "group"]
    id: NapCatId


class PokeData(StrictModel):
    """戳一戳消息数据。"""

    qq: NapCatId | None = None
    id: NapCatId | None = None
    type: NapCatId | None = None
    name: str | None = None


class LocationData(StrictModel):
    """位置消息数据。"""

    lat: str
    lon: str
    title: str | None = None
    content: str | None = None


class XmlData(StrictModel):
    """XML 消息数据。"""

    data: str


class MiniAppData(StrictModel):
    """小程序消息数据。"""

    content: str | JsonObject


class Text(BaseSegment[TextData]):
    """文本消息段。"""

    type: Literal["text"] = "text"
    _arg_key: ClassVar[str | None] = "text"


class At(BaseSegment[AtData]):
    """艾特消息段。"""

    type: Literal["at"] = "at"
    _arg_key: ClassVar[str | None] = "qq"


class Image(BaseSegment[ImageData]):
    """图片消息段。"""

    type: Literal["image"] = "image"
    _arg_key: ClassVar[str | None] = "file"


class Face(BaseSegment[FaceData]):
    """系统表情消息段。"""

    type: Literal["face"] = "face"
    _arg_key: ClassVar[str | None] = "id"


class Reply(BaseSegment[ReplyData]):
    """回复消息段。"""

    type: Literal["reply"] = "reply"
    _arg_key: ClassVar[str | None] = "id"


class Dice(BaseSegment[None | bool]):
    """骰子消息段。"""

    type: Literal["dice"] = "dice"


class Rps(BaseSegment[None | bool]):
    """猜拳消息段。"""

    type: Literal["rps"] = "rps"


class Record(BaseSegment[RecordData]):
    """语音消息段。"""

    type: Literal["record"] = "record"
    _arg_key: ClassVar[str | None] = "file"


class Video(BaseSegment[VideoData]):
    """视频消息段。"""

    type: Literal["video"] = "video"
    _arg_key: ClassVar[str | None] = "file"


class File(BaseSegment[FileData]):
    """文件消息段。"""

    type: Literal["file"] = "file"
    _arg_key: ClassVar[str | None] = "file"


class Json(BaseSegment[JsonData]):
    """JSON 消息段。"""

    type: Literal["json"] = "json"
    _arg_key: ClassVar[str | None] = "data"


class Forward(BaseSegment[ForwardData]):
    """合并转发消息段。"""

    type: Literal["forward"] = "forward"
    _arg_key: ClassVar[str | None] = "id"


class Node(BaseSegment[NodeData]):
    """合并转发节点消息段。"""

    type: Literal["node"] = "node"


class Music(BaseSegment[MusicData]):
    """音乐消息段。"""

    type: Literal["music"] = "music"


class MFace(BaseSegment[MFaceData]):
    """商城表情消息段。"""

    type: Literal["mface"] = "mface"
    _arg_key: ClassVar[str | None] = "emoji_id"


class Markdown(BaseSegment[MarkdownData]):
    """Markdown 消息段。"""

    type: Literal["markdown"] = "markdown"
    _arg_key: ClassVar[str | None] = "content"


class Contact(BaseSegment[ContactData]):
    """推荐联系人或群聊消息段。"""

    type: Literal["contact"] = "contact"


class Poke(BaseSegment[PokeData]):
    """戳一戳消息段。"""

    type: Literal["poke"] = "poke"


class Location(BaseSegment[LocationData]):
    """位置消息段。"""

    type: Literal["location"] = "location"


class Xml(BaseSegment[XmlData]):
    """XML 消息段。"""

    type: Literal["xml"] = "xml"
    _arg_key: ClassVar[str | None] = "data"


class MiniApp(BaseSegment[MiniAppData]):
    """小程序消息段。"""

    type: Literal["miniapp"] = "miniapp"
    _arg_key: ClassVar[str | None] = "content"


type MessageSegment = Annotated[
    Text
    | At
    | Image
    | Face
    | Reply
    | Dice
    | Rps
    | Record
    | Video
    | File
    | Json
    | Forward
    | Node
    | Music
    | MFace
    | Markdown
    | Contact
    | Poke
    | Location
    | Xml
    | MiniApp,
    Field(discriminator="type"),
]
