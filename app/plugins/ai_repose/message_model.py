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
    
class GetGroupRootFiles(BaseModel):
    """获取群根目录下的所有文件"""
    group_id: Annotated[int, Field(description="群聊ID")]
    file_count: Annotated[int, Field(description="文件数量")]=50
class GetGroupFilesByFolder(BaseModel):
    """获取群目录下的所有文件"""
    folder_id: Annotated[int, Field(description="和 folder 二选一")]
    folder: Annotated[str, Field(description="和 folder_id 二选一")]
    file_count: Annotated[int, Field(description="文件数量")]=50
