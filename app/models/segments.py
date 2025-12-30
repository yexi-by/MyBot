from typing import Literal, ClassVar, Any, cast
from pydantic import BaseModel

# 示例
# Text.new("你好世界")
# Text.new(text="你好世界")
# Rps.new() 骰子和猜拳等不需要参数


class BaseSegment[T](BaseModel):
    """
    消息段通用基类
    T: 对应的 Data 类类型
    """

    data: T

    _arg_key: ClassVar[str | None] = None

    @classmethod
    def new(cls, arg: Any = None, **kwargs):
        data_cls = cls.model_fields["data"].annotation
        if isinstance(data_cls, type) and issubclass(data_cls, BaseModel):
            if arg is not None and cls._arg_key:
                kwargs[cls._arg_key] = arg
            return cls(data=cast(Any, data_cls(**kwargs)))
        return cls(data=arg)


type MessageSegment = (
    Text | At | Image | Reply | Face | Dice | Rps | File | Video | Record
)


class TextData(BaseModel):
    text: str  # 文本内容 -发送|接收


class AtData(BaseModel):
    qq: Literal["all"] | int  # 要@的QQ号，使用"all"表示@全体成员 -发送|接收


class ImageData(BaseModel):
    file: str  # "base64://"+base64编码
    url: str | None = None
    local_path: str | None = None  # 本地私有路径 用于数据库私有传输


class ReplyData(BaseModel):
    id: int  # 被回复消息的唯一 ID -发送|接收


class FaceData(BaseModel):
    id: int  # 表情 ID -发送|接收
    raw: Any = None  # 表情原始数据 -接收
    resultId: str | None = None  # 骰子或石头剪刀布结果 ID -接收
    chainCount: int | None = None  # 连续发送次数 -接收


class FileData(BaseModel):
    file: str  # "base64://"+base64编码 -发送
    name: str | None = None  # 文件名(可选) -发送
    file_id: str | None = None  # 文件 ID -接收
    file_size: int | None = None  # 文件大小(字节) -接收


class VideoData(BaseModel):
    file: str  # "base64://"+base64编码 -发送
    url: str | None = None  # 视频在线 URL -接收
    file_size: int | None = None  # 文件大小(字节) -接收
    local_path: str | None = None  # 本地私有路径 用于数据库私有传输


class RecordData(BaseModel):
    file: str  # "base64://"+base64编码 -发送
    file_size: int | None = None  # 文件大小(字节) -接收
    path: str | None = None  # 文件路径 -接收


class Text(BaseSegment[TextData]):
    """发送文本"""

    type: Literal["text"] = "text"
    _arg_key = "text"


class At(BaseSegment[AtData]):
    """发送群艾特"""

    type: Literal["at"] = "at"
    _arg_key = "qq"


class Image(BaseSegment[ImageData]):
    """发送图片"""

    type: Literal["image"] = "image"
    _arg_key = "file"


class Reply(BaseSegment[ReplyData]):
    """发送回复"""

    type: Literal["reply"] = "reply"
    _arg_key = "id"


class Face(BaseSegment[FaceData]):
    """发送系统表情"""

    type: Literal["face"] = "face"
    _arg_key = "id"


class Dice(BaseSegment[None | bool]):
    """发送骰子"""

    type: Literal["dice"] = "dice"


class Rps(BaseSegment[None | bool]):
    """发送猜拳"""

    type: Literal["rps"] = "rps"


class File(BaseSegment[FileData]):
    """发送文件"""

    type: Literal["file"] = "file"
    _arg_key = "file"


class Video(BaseSegment[VideoData]):
    """发送视频"""

    type: Literal["video"] = "video"
    _arg_key = "file"


class Record(BaseSegment[RecordData]):
    """发送语音"""

    type: Literal["record"] = "record"
    _arg_key = "file"
