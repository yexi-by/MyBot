"""AI 群聊工具循环测试。"""

import unittest
from typing import cast

from app.models import GroupMessage, JsonObject, Response, Sender, Text
from app.plugins.ai_group_chat.config import AIGroupChatConfig
from app.plugins.ai_group_chat.tool_loop import GroupChatToolLoop
from app.plugins.base import Context
from app.services import ChatMessage, ContextHandler
from app.services.llm.schemas import LLMResponse, LLMToolChoice, LLMToolDefinition


class FakeBot:
    """测试用 Bot，记录发出的群文本。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.sent_texts: list[str] = []

    async def send_msg(self, *, group_id: str, text: str) -> Response:
        """记录发送文本并返回成功响应。"""
        _ = group_id
        self.sent_texts.append(text)
        return Response(status="ok", retcode=0)


class FakeContext:
    """测试用插件上下文，只提供本测试需要的 Bot。"""

    def __init__(self) -> None:
        """初始化测试依赖。"""
        self.bot: FakeBot = FakeBot()
        self.database: FakeDatabase = FakeDatabase()
        self.llm: FakeLLM = FakeLLM()
        self.mcp_tool_manager: FakeMCPToolManager = FakeMCPToolManager()


class FakeDatabase:
    """测试用空数据库。"""


class FakeLLM:
    """测试用 LLM，总是返回带思维链的正式回复。"""

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """返回无工具调用的模型响应。"""
        _ = (messages, model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        return LLMResponse(content="正式回复", reasoning_content="模型思考")


class FakeMCPToolManager:
    """测试用 MCP 管理器，不暴露任何工具。"""

    def list_tools(self) -> list[LLMToolDefinition]:
        """返回空工具列表。"""
        return []

    async def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        """测试中不会调用工具。"""
        _ = (name, arguments)
        return {"ok": True}


def build_config(*, output_reasoning_content: bool) -> AIGroupChatConfig:
    """构造测试用 AI 群聊配置。"""
    return AIGroupChatConfig(
        model_name="gpt-5.5",
        model_vendors="CLIProxyAPI",
        output_reasoning_content=output_reasoning_content,
        group_config=[],
    )


def build_message() -> GroupMessage:
    """构造测试用群消息。"""
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id="30000",
        group_id="40000",
        group_name="测试群",
        message=[Text.new("你好呀")],
        raw_message="你好呀",
        sender=Sender(user_id="20000", nickname="夜袭", role="member"),
    )


class GroupChatToolLoopTest(unittest.IsolatedAsyncioTestCase):
    """验证 AI 群聊回复展示和上下文写入策略。"""

    async def test_reasoning_output_is_visible_but_not_persisted(self) -> None:
        """开启思维链展示时，长期上下文只保存正式回复。"""
        fake_context = FakeContext()
        tool_loop = GroupChatToolLoop(
            config=build_config(output_reasoning_content=True),
            context=cast(Context, fake_context),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_length=10)

        await tool_loop.run(
            msg=build_message(),
            chat_handler=chat_handler,
            turn_messages=[ChatMessage(role="user", text="用户消息")],
        )

        self.assertEqual(
            fake_context.bot.sent_texts,
            [
                "【模型原生思维链】\n"
                "---\n"
                "模型思考\n"
                "---\n\n"
                "【回复】\n"
                "正式回复"
            ],
        )
        self.assertEqual(chat_handler.messages_lst[-1].role, "assistant")
        self.assertEqual(chat_handler.messages_lst[-1].text, "正式回复")
