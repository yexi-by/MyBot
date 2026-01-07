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
            description="要艾特的人的qq号。填 'all' 则艾特所有人。注意：如果已经使用 reply_to_message_id 回复某人，无需再填此字段，否则会重复艾特影响体验"
        ),
    ] = None
    reply_to_message_id: Annotated[
        int | None,
        Field(
            description="需要回复的那条消息的ID。重要：QQ回复消息会自动艾特被回复者，因此使用此字段时不要同时填写 at 字段，避免重复艾特"
        ),
    ] = None
