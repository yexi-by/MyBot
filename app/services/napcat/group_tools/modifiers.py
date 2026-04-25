"""NapCat 群聊消息修饰工具。"""

from typing import ClassVar, Literal

from app.models import At, GroupMessage, JsonObject, JsonValue, MessageSegment, NapCatId, Reply, Response, Text, to_json_value
from app.services.llm.tools import LLMToolRegistry

from .arguments import MENTION_ALL, MentionUserArgs, ReplyCurrentMessageArgs
from .protocols import NapCatGroupToolBot


class GroupMessageModifierToolset:
    """管理本轮最终群消息的艾特、引用等修饰动作。"""

    tool_names: ClassVar[frozenset[str]] = frozenset(
        {"qq__mention_user", "qq__reply_current_message"}
    )

    def __init__(
        self,
        *,
        bot: NapCatGroupToolBot,
        event: GroupMessage,
        allow_mention_all: bool,
    ) -> None:
        """绑定当前群事件与权限开关。"""
        self.bot: NapCatGroupToolBot = bot
        self.event: GroupMessage = event
        self.allow_mention_all: bool = allow_mention_all
        self._mentioned_user_ids: list[NapCatId | Literal["all"]] = []
        self._reply_message_id: NapCatId | None = None

    @property
    def mentioned_user_ids(self) -> tuple[NapCatId | Literal["all"], ...]:
        """返回最终回复需要艾特的用户列表。"""
        return tuple(self._mentioned_user_ids)

    @property
    def reply_message_id(self) -> NapCatId | None:
        """返回最终回复需要引用的消息 ID。"""
        return self._reply_message_id

    @classmethod
    def is_modifier_tool(cls, name: str) -> bool:
        """判断工具是否只用于修饰同轮最终群消息。"""
        return name in cls.tool_names

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记消息修饰工具。"""
        registry.register_tool(
            name="qq__mention_user",
            description=(
                "消息修饰工具：让本轮最终群回复艾特指定群成员或全体成员。"
                "它只修饰最终消息，不查询信息，也不会立即发送文本。"
                "调用此工具时，同一轮 assistant 必须输出非空 content 作为最终群回复正文；"
                "不要把正文写进参数。"
                "user_id 填具体 QQ 号时艾特该群成员；填字符串 all 时尝试艾特全体成员。"
                "如果当前配置关闭了 @全体，工具会返回错误结果，看到错误后请改用普通文本或艾特具体成员。"
                "不要和 mcp__ 开头的信息工具在同一轮调用；需要外部信息时先调用信息工具，"
                "拿到结果后再决定是否艾特。"
            ),
            parameters_model=MentionUserArgs,
            handler=self.mention_user,
        )
        registry.register_tool(
            name="qq__reply_current_message",
            description=(
                "消息修饰工具：让本轮最终群回复引用当前正在处理的这条群消息。"
                "它只修饰最终消息，不查询信息，也不会立即发送文本。"
                "调用此工具时，同一轮 assistant 必须输出非空 content 作为最终群回复正文；"
                "不要等待下一轮再给正文。"
                "不要和 mcp__ 开头的信息工具在同一轮调用；需要外部信息时先调用信息工具，"
                "拿到结果后再决定是否引用当前消息。"
            ),
            parameters_model=ReplyCurrentMessageArgs,
            handler=self.reply_current_message,
        )

    def clear(self) -> None:
        """清空本轮已登记但尚未发送的消息修饰动作。"""
        self._mentioned_user_ids.clear()
        self._reply_message_id = None

    def build_final_message_segments(self, text: str) -> list[MessageSegment]:
        """将模型最终文本和已登记的工具动作组装成 NapCat 消息段。"""
        if text.strip() == "":
            raise ValueError("最终群回复文本不能为空")
        final_text = self._format_text_after_mentions(text=text)
        message_segments: list[MessageSegment] = []
        if self._reply_message_id is not None:
            message_segments.append(Reply.new(self._reply_message_id))
        for user_id in self._mentioned_user_ids:
            message_segments.append(At.new(user_id))
        message_segments.append(Text.new(final_text))
        return message_segments

    async def send_final_text(self, text: str) -> Response:
        """向当前群发送模型最终文本，并应用艾特和回复修饰。"""
        return await self.bot.send_msg(
            group_id=self.event.group_id,
            message_segment=self.build_final_message_segments(text),
        )

    async def mention_user(self, arguments: JsonObject) -> JsonValue:
        """登记最终回复需要艾特的群成员。"""
        user_id = self._read_user_id(arguments)
        if user_id == MENTION_ALL and not self.allow_mention_all:
            return {
                "ok": False,
                "error": "当前群聊配置关闭了 @全体 能力，不能艾特全体成员。请改用普通文本回复，或只艾特具体群成员。",
                "effect": "mention_all_rejected",
                "group_id": to_json_value(self.event.group_id),
                "user_id": MENTION_ALL,
            }
        if user_id not in self._mentioned_user_ids:
            self._mentioned_user_ids.append(user_id)
        return {
            "ok": True,
            "effect": "final_group_message_will_mention_user",
            "group_id": to_json_value(self.event.group_id),
            "user_id": to_json_value(user_id),
        }

    async def reply_current_message(self, arguments: JsonObject) -> JsonValue:
        """登记最终回复需要引用当前群消息。"""
        _ = arguments
        self._reply_message_id = self.event.message_id
        return {
            "ok": True,
            "effect": "final_group_message_will_reply_current_message",
            "group_id": to_json_value(self.event.group_id),
            "message_id": to_json_value(self.event.message_id),
        }

    def _format_text_after_mentions(self, *, text: str) -> str:
        """存在艾特动作时，在正文前补空格，避免显示成 `@昵称正文`。"""
        if not self._mentioned_user_ids:
            return text
        if text.startswith((" ", "\n", "\t")):
            return text
        return f" {text}"

    def _read_user_id(self, arguments: JsonObject) -> NapCatId | Literal["all"]:
        """从已校验参数中读取用户 ID。"""
        raw_user_id = arguments.get("user_id")
        if isinstance(raw_user_id, str):
            if raw_user_id.lower() == MENTION_ALL:
                return MENTION_ALL
            return raw_user_id
        raise TypeError("user_id 必须是字符串")
