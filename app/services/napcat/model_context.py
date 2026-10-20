"""群消息进入模型上下文时的异常机器人正文隔离，不修改原始聊天记录。"""

from app.database import StoredGroupMessage
from app.models import Markdown, Text
from app.services.llm.response_validation import (
    QUARANTINED_ASSISTANT_TEXT,
    has_response_control_content,
)


def isolate_bot_message_text(
    message: StoredGroupMessage, *, bot_id: str, text: str
) -> str:
    """检查自己的原始文字段和实际展开文本；群成员消息保持原样。"""
    if message.direction != "outgoing" and message.sender_id != bot_id:
        return text
    # 在截断前拼接文字段，避免标记被拆成多个消息段后漏检。
    raw_text = "".join(
        segment.data.text if isinstance(segment, Text) else segment.data.content
        for segment in message.segments
        if isinstance(segment, (Text, Markdown))
    )
    if has_response_control_content(raw_text) or has_response_control_content(text):
        return QUARANTINED_ASSISTANT_TEXT
    return text
