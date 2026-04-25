"""NapCat 群聊控制工具。"""

from typing import ClassVar

from app.models import JsonObject, JsonValue, to_json_value
from app.services.llm.tools import LLMToolRegistry

from .arguments import FinishConversationArgs


class GroupConversationControlToolset:
    """管理当前群聊处理流程的控制动作。"""

    tool_names: ClassVar[frozenset[str]] = frozenset({"qq__finish_conversation"})

    def __init__(self) -> None:
        """初始化控制状态。"""
        self._finished: bool = False

    @property
    def conversation_finished(self) -> bool:
        """返回模型是否请求结束本次群聊处理。"""
        return self._finished

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记群聊控制工具。"""
        registry.register_tool(
            name="qq__finish_conversation",
            description="结束本次群聊处理。",
            parameters_model=FinishConversationArgs,
            handler=self.finish_conversation,
        )

    async def finish_conversation(self, arguments: JsonObject) -> JsonValue:
        """登记本次群聊处理已经结束。"""
        _ = FinishConversationArgs.model_validate(arguments)
        self._finished = True
        return to_json_value({"ok": True, "effect": "conversation_finished"})
