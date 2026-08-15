"""群聊合并转发外层消息引用解析。"""

from dataclasses import dataclass

from app.database import GroupDataScope, GroupMessageReader
from app.models import Forward, GroupMessage, NapCatId


@dataclass(frozen=True)
class ActiveForwardReference:
    """已经过当前群与撤回状态校验的合并转发引用。"""

    group_message_id: str
    forward_id: NapCatId


@dataclass(frozen=True)
class ActiveForwardReferenceError:
    """外层群消息不能唯一确定合并转发时的可恢复错误。"""

    group_message_id: str
    error_type: str
    error: str


async def resolve_active_forward_reference(
    *,
    group_messages: GroupMessageReader,
    event: GroupMessage,
    group_message_id: str,
) -> ActiveForwardReference | ActiveForwardReferenceError:
    """只从当前机器人、当前群的未撤回外层消息解析一个顶层 Forward。"""
    stored_message = await group_messages.get_active(
        scope=GroupDataScope(bot_id=event.self_id, group_id=event.group_id),
        message_id=group_message_id,
    )
    if stored_message is None:
        return ActiveForwardReferenceError(
            group_message_id=group_message_id,
            error_type="ActiveGroupMessageNotFound",
            error="当前机器人和群中没有找到这条未撤回消息。",
        )

    forward_segments = [
        segment for segment in stored_message.segments if isinstance(segment, Forward)
    ]
    if not forward_segments:
        return ActiveForwardReferenceError(
            group_message_id=group_message_id,
            error_type="ForwardSegmentNotFound",
            error="指定的外层群消息不含顶层合并转发段。",
        )
    if len(forward_segments) > 1:
        return ActiveForwardReferenceError(
            group_message_id=group_message_id,
            error_type="AmbiguousForwardSegments",
            error="指定的外层群消息含有多个顶层合并转发段，无法唯一确定目标。",
        )
    return ActiveForwardReference(
        group_message_id=group_message_id,
        forward_id=forward_segments[0].data.id,
    )
