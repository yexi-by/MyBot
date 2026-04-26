"""NapCat 群聊 content 消息标记解析。"""

import re
from dataclasses import dataclass
from typing import Final, Literal

from app.models import At, GroupMessage, MessageSegment, NapCatId, Reply, Response, Text

from .arguments import MENTION_ALL
from .protocols import NapCatGroupToolBot


@dataclass(frozen=True)
class GroupMessageDirectives:
    """描述模型 content 中解析出的群消息修饰标记。"""

    clean_text: str
    mentioned_user_ids: tuple[NapCatId | Literal["all"], ...]
    reply_current_message: bool

    @property
    def has_directives(self) -> bool:
        """判断当前 content 是否包含可执行的群消息修饰标记。"""
        return self.reply_current_message or bool(self.mentioned_user_ids)


class GroupMessageDirectiveParser:
    """把模型 content 中的 `<Reply>` 和 `<At>` 标记转换为 NapCat 消息段。"""

    reply_pattern: Final[re.Pattern[str]] = re.compile(r"<Reply>\s*")
    at_pattern: Final[re.Pattern[str]] = re.compile(r"<At>\s*([^<]*?)\s*</At>")
    malformed_at_pattern: Final[re.Pattern[str]] = re.compile(r"</?At\b[^>]*>")

    def __init__(
        self,
        *,
        bot: NapCatGroupToolBot,
        event: GroupMessage,
        allow_mention_all: bool,
    ) -> None:
        """绑定当前群事件与 @全体权限开关。"""
        self.bot: NapCatGroupToolBot = bot
        self.event: GroupMessage = event
        self.allow_mention_all: bool = allow_mention_all

    def parse(self, *, content: str) -> GroupMessageDirectives:
        """解析模型 content，返回清理后的正文和待执行的消息修饰动作。"""
        reply_current_message = self.reply_pattern.search(content) is not None
        content_without_reply = self.reply_pattern.sub("", content)
        mentioned_user_ids: list[NapCatId | Literal["all"]] = []
        errors: list[str] = []

        def replace_at(match: re.Match[str]) -> str:
            target = match.group(1).strip()
            if target == "":
                errors.append("<At> 标记里的 QQ 号不能为空。")
                return ""
            user_id = self._validate_mention_target(target=target, errors=errors)
            if user_id is not None and user_id not in mentioned_user_ids:
                mentioned_user_ids.append(user_id)
            return ""

        clean_text = self.at_pattern.sub(replace_at, content_without_reply)
        if self.malformed_at_pattern.search(clean_text):
            errors.append(
                "<At> 标记格式错误，请使用 `<At>QQ号</At>` 或 `<At>all</At>`。"
            )
        clean_text = clean_text.strip()
        if clean_text == "":
            errors.append("去掉 <Reply> 和 <At> 标记后，群回复正文不能为空。")
        if errors:
            raise ValueError("；".join(dict.fromkeys(errors)))
        return GroupMessageDirectives(
            clean_text=clean_text,
            mentioned_user_ids=tuple(mentioned_user_ids),
            reply_current_message=reply_current_message,
        )

    def build_message_segments(self, *, content: str) -> list[MessageSegment]:
        """把模型 content 转换为 NapCat 可发送的消息段。"""
        directives = self.parse(content=content)
        final_text = self._format_text_after_mentions(
            text=directives.clean_text,
            mentioned_user_ids=directives.mentioned_user_ids,
        )
        message_segments: list[MessageSegment] = []
        if directives.reply_current_message:
            message_segments.append(Reply.new(self.event.message_id))
        for user_id in directives.mentioned_user_ids:
            message_segments.append(At.new(user_id))
        message_segments.append(Text.new(final_text))
        return message_segments

    async def send_content(self, *, content: str) -> Response:
        """发送模型 content，并应用 content 中声明的群消息修饰标记。"""
        return await self.bot.send_msg(
            group_id=self.event.group_id,
            message_segment=self.build_message_segments(content=content),
        )

    def _validate_mention_target(
        self, *, target: str, errors: list[str]
    ) -> NapCatId | Literal["all"] | None:
        """校验 `<At>` 目标，只允许 QQ 号或 all。"""
        if target.lower() == MENTION_ALL:
            if not self.allow_mention_all:
                errors.append("当前群聊配置关闭了 @全体 能力，请不要使用 <At>all</At>。")
                return None
            return MENTION_ALL
        if not target.isdecimal():
            errors.append(f"<At> 只支持 QQ 号或 all，当前值是 `{target}`。")
            return None
        return target

    def _format_text_after_mentions(
        self,
        *,
        text: str,
        mentioned_user_ids: tuple[NapCatId | Literal["all"], ...],
    ) -> str:
        """存在艾特动作时，在正文前补空格，避免显示成 `@昵称正文`。"""
        if not mentioned_user_ids:
            return text
        if text.startswith((" ", "\n", "\t")):
            return text
        return f" {text}"
