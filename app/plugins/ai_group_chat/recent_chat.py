"""被艾特时随本轮附带的最近群聊记录。"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime

from app.database import StoredGroupMessage
from app.models import (
    At,
    Contact,
    Dice,
    Face,
    File,
    Forward,
    Image,
    LightApp,
    Location,
    MFace,
    Markdown,
    MessageSegment,
    Music,
    Node,
    Poke,
    Record,
    Reply,
    Rps,
    Share,
    Text,
    Video,
)
from app.services import ChatMessage

from .constants import BEIJING_TIMEZONE


class RecentChatFormatter:
    """把最近群消息压缩成一行一条、像聊天记录一样的文本。"""

    def __init__(self, *, max_chars_per_message: int) -> None:
        """保存单条消息的截断长度，0 表示不截断。"""
        self.max_chars_per_message: int = max_chars_per_message

    def build_message(
        self,
        *,
        messages: Sequence[StoredGroupMessage],
        bot_id: str,
        group_name: str | None,
        now: datetime,
    ) -> ChatMessage | None:
        """按时间正序生成本轮临时附带的群聊记录，没有消息时返回 None。"""
        if not messages:
            return None
        names = {
            message.sender_id: name
            for message in messages
            if (name := message.sender_name.strip())
        }
        senders = {message.message_id: message.sender_id for message in messages}
        today = now.astimezone(BEIJING_TIMEZONE).date()
        lines = [
            self._format_line(
                message=message,
                bot_id=bot_id,
                names=names,
                senders=senders,
                today=today,
            )
            for message in messages
        ]
        group_label = f"群「{group_name.strip()}」" if group_name and group_name.strip() else "群"
        header = (
            f"【{group_label}里最近的聊天】\n"
            f"下面是这条 @ 之前最近的 {len(messages)} 条群消息，按时间排列，"
            "帮你了解大家刚才在聊什么；标“你”的是你自己发的。"
            "不用逐条回复，重点回应后面 @ 你的那条。"
        )
        return ChatMessage(role="user", text="\n".join([header, *lines]))

    def _format_line(
        self,
        *,
        message: StoredGroupMessage,
        bot_id: str,
        names: Mapping[str, str],
        senders: Mapping[str, str],
        today: date,
    ) -> str:
        """格式化一条聊天记录：时间、说话人和压缩后的正文。"""
        local_time = message.occurred_at.astimezone(BEIJING_TIMEZONE)
        time_text = (
            local_time.strftime("%H:%M")
            if local_time.date() == today
            else local_time.strftime("%m-%d %H:%M")
        )
        if message.direction == "outgoing" or message.sender_id == bot_id:
            speaker = "你"
        else:
            speaker = f"{names.get(message.sender_id, '群友')}({message.sender_id})"
        content = self._format_content(
            message=message,
            bot_id=bot_id,
            names=names,
            senders=senders,
        )
        return f"[{time_text}] {speaker}：{content}"

    def _format_content(
        self,
        *,
        message: StoredGroupMessage,
        bot_id: str,
        names: Mapping[str, str],
        senders: Mapping[str, str],
    ) -> str:
        """把消息段压成单行文本，非文字内容只保留简短占位。"""
        parts = [
            self._format_segment(
                segment=segment,
                message_id=message.message_id,
                bot_id=bot_id,
                names=names,
                senders=senders,
            )
            for segment in message.segments
        ]
        content = " ".join("".join(parts).split())
        if content == "":
            return "[空消息]"
        limit = self.max_chars_per_message
        if limit > 0 and len(content) > limit:
            return content[:limit] + "…"
        return content

    def _format_segment(
        self,
        *,
        segment: MessageSegment,
        message_id: str,
        bot_id: str,
        names: Mapping[str, str],
        senders: Mapping[str, str],
    ) -> str:
        """返回单个消息段在聊天记录中的简短写法。"""
        if isinstance(segment, Text):
            return segment.data.text
        if isinstance(segment, Markdown):
            return segment.data.content
        if isinstance(segment, At):
            if segment.data.qq == "all":
                return "@全体成员 "
            if segment.data.qq == bot_id:
                return "@你 "
            name = names.get(segment.data.qq) or segment.data.name or segment.data.qq
            return f"@{name} "
        if isinstance(segment, Reply):
            sender_id = senders.get(segment.data.id)
            if sender_id is None:
                return "[回复] "
            if sender_id == bot_id:
                return "[回复 你] "
            return f"[回复 {names.get(sender_id, sender_id)}] "
        if isinstance(segment, Image):
            summary = (segment.data.summary or "").strip()
            if summary and summary != "[图片]":
                return summary if summary.startswith("[") else f"[{summary}]"
            return "[动画表情]" if str(segment.data.sub_type) == "1" else "[图片]"
        if isinstance(segment, Face):
            raw = segment.data.raw
            face_text = raw.get("faceText") if isinstance(raw, dict) else None
            if isinstance(face_text, str) and face_text.strip("/[] "):
                return f"[{face_text.strip('/[] ')}]"
            return "[表情]"
        if isinstance(segment, MFace):
            summary = (segment.data.summary or "").strip()
            if summary:
                return summary if summary.startswith("[") else f"[{summary}]"
            return "[表情包]"
        if isinstance(segment, Forward):
            # 合并转发可用工具展开，必须保留外层消息 ID。
            return f"[合并转发，消息 ID {message_id}]"
        if isinstance(segment, File):
            return f"[文件 {segment.data.name or segment.data.file}]"
        if isinstance(segment, Share):
            return f"[链接 {segment.data.title}]" if segment.data.title else "[链接]"
        if isinstance(segment, Music):
            return f"[音乐 {segment.data.title}]" if segment.data.title else "[音乐]"
        if isinstance(segment, Location):
            return f"[位置 {segment.data.title}]" if segment.data.title else "[位置]"
        if isinstance(segment, LightApp):
            title = segment.data.title or segment.data.desc
            return f"[卡片 {title}]" if title else "[卡片]"
        if isinstance(segment, Record):
            return "[语音]"
        if isinstance(segment, Video):
            return "[视频]"
        if isinstance(segment, Node):
            return "[转发内容]"
        if isinstance(segment, Poke):
            return "[戳一戳]"
        if isinstance(segment, Dice):
            return "[骰子]"
        if isinstance(segment, Rps):
            return "[猜拳]"
        if isinstance(segment, Contact):
            return "[推荐名片]"
        return "[卡片]" if segment.type in ("json", "xml", "miniapp") else f"[{segment.type}]"
