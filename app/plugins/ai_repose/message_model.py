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

    group_id: Annotated[int, Field(description="目标群聊的群号 (group_id)")]
    file_count: Annotated[
        int, Field(description="需要获取的文件数量限制，默认为 50")
    ] = 50


class KwargsGroupFilesByFolder(BaseModel):
    """获取群子目录文件列表"""

    group_id: Annotated[int, Field(description="目标群聊的群号 (group_id)")]
    folder_id: Annotated[
        str | None,
        Field(
            description="文件夹ID。优先使用此字段。与 folder 字段二选一，用于指定要获取文件列表的子目录。"
        ),
    ] = None
    folder: Annotated[
        str | None,
        Field(
            description="文件夹路径/名称。与 folder_id 字段二选一。如果不知道 folder_id，可以使用此字段。"
        ),
    ] = None
    file_count: Annotated[
        int, Field(description="需要获取的文件数量限制，默认为 50")
    ] = 50


class KwargsGroupFile(BaseModel):
    """获取文件信息"""

    file_id: Annotated[
        str | None,
        Field(
            description="文件ID。优先使用此字段。与 file 字段二选一，用于指定要获取信息的具体文件。"
        ),
    ] = None
    file: Annotated[
        str | None, Field(description="文件路径/名称。与 file_id 字段二选一。")
    ] = None


class GroupFile(BaseModel):
    group_root_file: Annotated[
        KwargsGroupRootFiles | None,
        Field(description="当用户想要查看群根目录下的文件列表时，使用此字段。"),
    ] = None
    group_files_by_folder: Annotated[
        KwargsGroupFilesByFolder | None,
        Field(
            description="当用户想要查看群内某个特定文件夹（子目录）下的文件列表时，使用此字段。"
        ),
    ] = None
    group_file: Annotated[
        KwargsGroupFile | None,
        Field(
            description="当用户想要获取某个具体文件的详细信息（如下载链接）时，使用此字段。"
        ),
    ] = None
