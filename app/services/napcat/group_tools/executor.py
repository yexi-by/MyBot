"""NapCat 群聊工具执行器。"""

from typing import ClassVar, Literal, override

from app.models import GroupMessage, JsonObject, JsonValue, MessageSegment, NapCatId, Response
from app.services.llm.schemas import LLMToolDefinition, LLMToolExecutor
from app.services.llm.tools import LLMToolRegistry

from .control import GroupConversationControlToolset
from .files import GroupFileToolset
from .history import GroupHistoryToolset
from .modifiers import GroupMessageModifierToolset
from .protocols import NapCatGroupHistoryDatabase, NapCatGroupToolBot


class NapCatGroupToolExecutor(LLMToolExecutor):
    """聚合当前群可用的 NapCat 本地工具。"""

    MESSAGE_MODIFIER_TOOL_NAMES: ClassVar[frozenset[str]] = (
        GroupMessageModifierToolset.tool_names
    )
    CONVERSATION_FINISH_TOOL_NAMES: ClassVar[frozenset[str]] = (
        GroupConversationControlToolset.tool_names
    )

    def __init__(
        self,
        bot: NapCatGroupToolBot,
        database: NapCatGroupHistoryDatabase,
        event: GroupMessage,
        allow_mention_all: bool = False,
    ) -> None:
        """绑定当前群事件，并注册可供模型调用的群聊工具。"""
        self._registry: LLMToolRegistry = LLMToolRegistry()
        self._modifiers: GroupMessageModifierToolset = GroupMessageModifierToolset(
            bot=bot,
            event=event,
            allow_mention_all=allow_mention_all,
        )
        self._control: GroupConversationControlToolset = (
            GroupConversationControlToolset()
        )
        self._files: GroupFileToolset = GroupFileToolset(bot=bot, event=event)
        self._history: GroupHistoryToolset = GroupHistoryToolset(
            database=database,
            event=event,
        )
        self._register_tools()

    @property
    def mentioned_user_ids(self) -> tuple[NapCatId | Literal["all"], ...]:
        """返回最终回复需要艾特的用户列表。"""
        return self._modifiers.mentioned_user_ids

    @property
    def reply_message_id(self) -> NapCatId | None:
        """返回最终回复需要引用的消息 ID。"""
        return self._modifiers.reply_message_id

    @property
    def has_message_modifiers(self) -> bool:
        """返回当前是否存在待应用的消息修饰动作。"""
        return self._modifiers.has_modifiers

    @property
    def conversation_finished(self) -> bool:
        """返回模型是否请求结束本次群聊处理。"""
        return self._control.conversation_finished

    @classmethod
    def is_message_modifier_tool(cls, name: str) -> bool:
        """判断工具是否只用于修饰同轮最终群消息。"""
        return name in cls.MESSAGE_MODIFIER_TOOL_NAMES

    @classmethod
    def is_conversation_finish_tool(cls, name: str) -> bool:
        """判断工具是否用于显式结束本次群聊处理。"""
        return name in cls.CONVERSATION_FINISH_TOOL_NAMES

    @override
    def list_tools(self) -> list[LLMToolDefinition]:
        """返回当前群聊可用工具。"""
        return self._registry.list_tools()

    @override
    async def call_tool(self, name: str, arguments: JsonObject) -> JsonValue:
        """调用当前群聊工具。"""
        return await self._registry.call_tool(name=name, arguments=arguments)

    def clear_message_modifiers(self) -> None:
        """清空本轮已登记但尚未发送的消息修饰动作。"""
        self._modifiers.clear()

    def build_final_message_segments(self, text: str) -> list[MessageSegment]:
        """将模型最终文本和已登记的工具动作组装成 NapCat 消息段。"""
        return self._modifiers.build_final_message_segments(text=text)

    async def send_final_text(self, text: str) -> Response:
        """向当前群发送模型最终文本，并应用艾特和回复修饰。"""
        return await self._modifiers.send_final_text(text=text)

    def _register_tools(self) -> None:
        """注册当前群聊工具定义。"""
        self._modifiers.register_tools(self._registry)
        self._control.register_tools(self._registry)
        self._files.register_tools(self._registry)
        self._history.register_tools(self._registry)
