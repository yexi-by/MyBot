from typing import Annotated, Literal
from pydantic import BaseModel, Field


class MessageContent(BaseModel):
    """定义发送消息的结构，包含文本、艾特和回复功能"""

    text: Annotated[
        str | None,
        Field(description="发送的群聊文本内容。如果只想艾特人而不说话，可以为空字符串"),
    ] = None
    at: Annotated[
        int | Literal["all"] | None,
        Field(
            description="要艾特的人的qq号。填 'all' 则艾特所有人。禁止事项：如果使用了 reply_to_message_id，则绝对不要填写此字段，因为回复会自动艾特对方。"
        ),
    ] = None
    reply_to_message_id: Annotated[
        int | None,
        Field(
            description="需要回复的那条消息的ID。重要：QQ回复消息会自动艾特被回复者，因此使用此字段时不要同时填写 at 字段，避免重复艾特"
        ),
    ] = None


class KwargsGroupRootFiles(BaseModel):
    """获取群根目录文件列表"""

    file_count: Annotated[
        int, Field(description="需要获取的文件数量限制，默认为 50")
    ] = 50


class KwargsGroupFilesByFolder(BaseModel):
    """获取群子目录文件列表"""

    folder: Annotated[
        str,
        Field(description="文件夹ID,用于指定要获取文件列表的子目录。"),
    ]
    file_count: Annotated[
        int, Field(description="需要获取的文件数量限制，默认为 50")
    ] = 50


class KwargsGroupFile(BaseModel):
    """获取群文件下载链接"""
    file_id: Annotated[str, Field(description="需要下载的文件id")]


class GroupFile(BaseModel):
    group_root_file: Annotated[
        KwargsGroupRootFiles | None,
        Field(description="当想要查看群根目录下的文件列表时，使用此字段。"),
    ] = None
    group_files_by_folder: Annotated[
        KwargsGroupFilesByFolder | None,
        Field(
            description="当想要查看群内某个特定文件夹（子目录）下的文件列表时，使用此字段。"
        ),
    ] = None
    group_file: Annotated[
        KwargsGroupFile | None,
        Field(description="当需要读取群文件内容时使用此字段"),
    ] = None
