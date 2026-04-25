"""AI 群聊工具循环测试。"""

import unittest
from typing import Protocol, cast

from app.models import GroupMessage, JsonObject, MessageSegment, Response, Sender, Text
from app.plugins.ai_group_chat.config import AIGroupChatConfig
from app.plugins.ai_group_chat.debug_dump import AIGroupChatDebugDumper
from app.plugins.ai_group_chat.tool_loop import GroupChatToolLoop
from app.plugins.base import Context
from app.services import ChatMessage, ContextHandler
from app.services.llm.schemas import (
    LLMResponse,
    LLMToolCall,
    LLMToolChoice,
    LLMToolDefinition,
)


class FakeLLMProtocol(Protocol):
    """描述测试用 LLM 需要提供的异步响应接口。"""

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """返回测试用纯文本响应。"""
        ...

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """返回测试用结构化模型响应。"""
        ...


class FakeMCPToolManagerProtocol(Protocol):
    """描述测试用工具管理器需要提供的接口。"""

    def list_tools(self) -> list[LLMToolDefinition]:
        """返回测试用工具定义。"""
        ...

    async def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        """执行测试用工具调用。"""
        ...


class FakeBot:
    """测试用 Bot，记录发出的群文本。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.sent_texts: list[str] = []
        self.sent_segment_types: list[list[str]] = []

    async def send_msg(
        self,
        *,
        group_id: str,
        text: str | None = None,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """记录发送文本并返回成功响应。"""
        _ = group_id
        if message_segment is not None:
            self.sent_segment_types.append(
                [message_segment_item.type for message_segment_item in message_segment]
            )
            self.sent_texts.append(self._extract_text(message_segment=message_segment))
            return Response(status="ok", retcode=0)
        if text is not None:
            self.sent_texts.append(text)
        return Response(status="ok", retcode=0)

    def _extract_text(self, *, message_segment: list[MessageSegment]) -> str:
        """从消息段里提取文本，便于断言发送内容。"""
        text_parts = [
            segment.data.text for segment in message_segment if isinstance(segment, Text)
        ]
        return "".join(text_parts)


class FakeContext:
    """测试用插件上下文，只提供本测试需要的 Bot。"""

    def __init__(
        self,
        *,
        llm: FakeLLMProtocol | None = None,
        mcp_tool_manager: FakeMCPToolManagerProtocol | None = None,
    ) -> None:
        """初始化测试依赖。"""
        self.bot: FakeBot = FakeBot()
        self.database: FakeDatabase = FakeDatabase()
        self.llm: FakeLLMProtocol = llm if llm is not None else FakeLLM()
        self.mcp_tool_manager: FakeMCPToolManagerProtocol = (
            mcp_tool_manager
            if mcp_tool_manager is not None
            else FakeMCPToolManager()
        )


class FakeDatabase:
    """测试用空数据库。"""


class FakeLLM:
    """测试用 LLM，总是返回带思维链的正式回复。"""

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """测试中默认不会触发纯文本压缩请求。"""
        _ = (messages, model_vendors, model_name)
        return "压缩摘要"

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
        return LLMResponse(
            content="正式回复",
            reasoning_content="模型思考",
            tool_calls=[
                LLMToolCall(
                    id="call-finish",
                    name="qq__finish_conversation",
                    arguments={},
                )
            ],
        )


class FakeMCPToolManager:
    """测试用 MCP 管理器，不暴露任何工具。"""

    def list_tools(self) -> list[LLMToolDefinition]:
        """返回空工具列表。"""
        return []

    async def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        """测试中不会调用工具。"""
        _ = (name, arguments)
        return {"ok": True}


class FakeInfoToolManager:
    """测试用信息工具管理器，用于触发工具调用续问流程。"""

    def list_tools(self) -> list[LLMToolDefinition]:
        """返回一个测试信息工具。"""
        return [
            LLMToolDefinition(
                name="test__lookup",
                description="查询测试信息。",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            )
        ]

    async def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        """返回固定工具结果。"""
        _ = (name, arguments)
        return {"ok": True, "value": "查到了"}


class FakeToolCallLLM:
    """测试用 LLM，先调用信息工具，再给出最终回复。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.received_messages: list[list[ChatMessage]] = []

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """测试中不会触发纯文本压缩请求。"""
        _ = (messages, model_vendors, model_name)
        return "压缩摘要"

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """第一轮返回工具调用，第二轮返回最终正文。"""
        _ = (model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        self.received_messages.append(messages[:])
        if len(self.received_messages) == 1:
            return LLMResponse(
                content="我先查一下",
                reasoning_content="工具前思考",
                tool_calls=[
                    LLMToolCall(id="call-1", name="test__lookup", arguments={})
                ],
            )
        return LLMResponse(
            content="工具后回复",
            reasoning_content="最终思考",
            tool_calls=[
                LLMToolCall(
                    id="call-finish",
                    name="qq__finish_conversation",
                    arguments={},
                )
            ],
        )


class FakeModifierToolCallLLM:
    """测试用 LLM，调用消息修饰工具并同时给出正文。"""

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """测试中不会触发纯文本压缩请求。"""
        _ = (messages, model_vendors, model_name)
        return "压缩摘要"

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """返回引用当前消息的工具调用和同轮正文。"""
        _ = (messages, model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        return LLMResponse(
            content="收到喵",
            tool_calls=[
                LLMToolCall(
                    id="call-reply",
                    name="qq__reply_current_message",
                    arguments={},
                ),
                LLMToolCall(
                    id="call-finish",
                    name="qq__finish_conversation",
                    arguments={},
                ),
            ],
        )


class FakeNoToolContinueLLM:
    """测试用 LLM，先输出无工具正文，再显式结束。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.received_messages: list[list[ChatMessage]] = []

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """测试中不会触发纯文本压缩请求。"""
        _ = (messages, model_vendors, model_name)
        return "压缩摘要"

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """第一轮只输出正文，第二轮调用结束工具。"""
        _ = (model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        self.received_messages.append(messages[:])
        if len(self.received_messages) == 1:
            return LLMResponse(content="第一句")
        return LLMResponse(
            content="第二句",
            tool_calls=[
                LLMToolCall(
                    id="call-finish",
                    name="qq__finish_conversation",
                    arguments={},
                )
            ],
        )


class FakeCompressionLLM:
    """测试用 LLM，先压缩上下文，再返回最终回复。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.compression_messages: list[ChatMessage] = []
        self.received_messages: list[list[ChatMessage]] = []

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """记录压缩请求并返回固定摘要。"""
        _ = (model_vendors, model_name)
        self.compression_messages = messages[:]
        return "旧上下文摘要：用户之前在讨论上下文压缩。"

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """记录正式请求并返回最终正文。"""
        _ = (model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        self.received_messages.append(messages[:])
        return LLMResponse(
            content="压缩后回复",
            tool_calls=[
                LLMToolCall(
                    id="call-finish",
                    name="qq__finish_conversation",
                    arguments={},
                )
            ],
        )


def build_config(
    *,
    output_reasoning_content: bool,
    pass_back_reasoning_content: bool = False,
    model_name: str = "gpt-5.5",
    context_compression_notice: str = "上下文有点长，我先整理一下记忆，稍等我几秒喵~",
) -> AIGroupChatConfig:
    """构造测试用 AI 群聊配置。"""
    return AIGroupChatConfig(
        model_name=model_name,
        model_vendors="CLIProxyAPI",
        output_reasoning_content=output_reasoning_content,
        pass_back_reasoning_content=pass_back_reasoning_content,
        context_compression_notice=context_compression_notice,
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
        config = build_config(output_reasoning_content=True)
        tool_loop = GroupChatToolLoop(
            config=config,
            context=cast(Context, fake_context),
            debug_dumper=AIGroupChatDebugDumper(config=config),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)

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
        self.assertIsNone(chat_handler.messages_lst[-1].reasoning_content)

    async def test_reasoning_can_be_passed_back_as_structured_field(self) -> None:
        """开启思维链回传时，长期上下文保留结构化字段但正文仍然干净。"""
        fake_context = FakeContext()
        config = build_config(
            output_reasoning_content=False,
            pass_back_reasoning_content=True,
        )
        tool_loop = GroupChatToolLoop(
            config=config,
            context=cast(Context, fake_context),
            debug_dumper=AIGroupChatDebugDumper(config=config),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)

        await tool_loop.run(
            msg=build_message(),
            chat_handler=chat_handler,
            turn_messages=[ChatMessage(role="user", text="用户消息")],
        )

        self.assertEqual(fake_context.bot.sent_texts, ["正式回复"])
        self.assertEqual(chat_handler.messages_lst[-1].role, "assistant")
        self.assertEqual(chat_handler.messages_lst[-1].text, "正式回复")
        self.assertEqual(
            chat_handler.messages_lst[-1].reasoning_content,
            "模型思考",
        )

    async def test_tool_call_reasoning_is_passed_back_when_enabled(self) -> None:
        """开启思维链回传时，工具调用续问也携带上一轮结构化思维链。"""
        fake_llm = FakeToolCallLLM()
        fake_context = FakeContext(
            llm=fake_llm,
            mcp_tool_manager=FakeInfoToolManager(),
        )
        config = build_config(
            output_reasoning_content=False,
            pass_back_reasoning_content=True,
        )
        tool_loop = GroupChatToolLoop(
            config=config,
            context=cast(Context, fake_context),
            debug_dumper=AIGroupChatDebugDumper(config=config),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)

        await tool_loop.run(
            msg=build_message(),
            chat_handler=chat_handler,
            turn_messages=[ChatMessage(role="user", text="用户消息")],
        )

        second_request_messages = fake_llm.received_messages[1]
        tool_call_message = next(
            message for message in second_request_messages if message.tool_calls
        )
        self.assertEqual(tool_call_message.role, "assistant")
        self.assertEqual(tool_call_message.reasoning_content, "工具前思考")
        self.assertEqual(fake_context.bot.sent_texts, ["我先查一下", "工具后回复"])
        self.assertEqual(chat_handler.messages_lst[2].text, "我先查一下")
        self.assertEqual(chat_handler.messages_lst[3].text, "工具后回复")

    async def test_modifier_tool_content_is_sent_with_message_segments(self) -> None:
        """消息修饰工具同轮 content 会按回复/艾特等消息段发送并写入上下文。"""
        fake_context = FakeContext(llm=FakeModifierToolCallLLM())
        config = build_config(output_reasoning_content=False)
        tool_loop = GroupChatToolLoop(
            config=config,
            context=cast(Context, fake_context),
            debug_dumper=AIGroupChatDebugDumper(config=config),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)

        await tool_loop.run(
            msg=build_message(),
            chat_handler=chat_handler,
            turn_messages=[ChatMessage(role="user", text="回复我")],
        )

        self.assertEqual(fake_context.bot.sent_texts, ["收到喵"])
        self.assertEqual(fake_context.bot.sent_segment_types, [["reply", "text"]])
        self.assertEqual(chat_handler.messages_lst[-1].role, "assistant")
        self.assertEqual(chat_handler.messages_lst[-1].text, "收到喵")

    async def test_plain_content_continues_until_finish_tool(self) -> None:
        """无工具正文会发群并继续，直到模型调用结束工具。"""
        fake_llm = FakeNoToolContinueLLM()
        fake_context = FakeContext(llm=fake_llm)
        config = build_config(output_reasoning_content=False)
        tool_loop = GroupChatToolLoop(
            config=config,
            context=cast(Context, fake_context),
            debug_dumper=AIGroupChatDebugDumper(config=config),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)

        await tool_loop.run(
            msg=build_message(),
            chat_handler=chat_handler,
            turn_messages=[ChatMessage(role="user", text="继续说")],
        )

        self.assertEqual(fake_context.bot.sent_texts, ["第一句", "第二句"])
        self.assertEqual(len(fake_llm.received_messages), 2)
        self.assertEqual(fake_llm.received_messages[1][-1].role, "assistant")
        self.assertEqual(fake_llm.received_messages[1][-1].text, "第一句")
        self.assertEqual(chat_handler.messages_lst[-2].text, "第一句")
        self.assertEqual(chat_handler.messages_lst[-1].text, "第二句")

    async def test_context_compression_excludes_current_message_then_rebuilds(
        self,
    ) -> None:
        """上下文超预算时，先压缩旧历史，再用摘要、当前消息和角色要求重建请求。"""
        fake_llm = FakeCompressionLLM()
        fake_context = FakeContext(llm=fake_llm)
        config = build_config(
            output_reasoning_content=False,
            model_name="deepseek-v4-pro",
            context_compression_notice="我先整理一下记忆喵~",
        )
        config.enable_deepseek_v4_roleplay_instruct = True
        tool_loop = GroupChatToolLoop(
            config=config,
            context=cast(Context, fake_context),
            debug_dumper=AIGroupChatDebugDumper(config=config),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=8000)
        chat_handler.build_chatmessage(
            message_lst=[
                ChatMessage(role="user", text="旧消息一" * 100),
                ChatMessage(role="assistant", text="旧回复一" * 100),
            ]
        )

        await tool_loop.run(
            msg=build_message(),
            chat_handler=chat_handler,
            turn_messages=[ChatMessage(role="user", text="当前新消息")],
        )

        compression_user_text = fake_llm.compression_messages[1].text or ""
        self.assertIn("旧消息一", compression_user_text)
        self.assertNotIn("当前新消息", compression_user_text)
        self.assertEqual(
            fake_context.bot.sent_texts,
            ["我先整理一下记忆喵~", "压缩后回复"],
        )
        final_messages = fake_llm.received_messages[0]
        self.assertEqual([message.role for message in final_messages], ["system", "user"])
        final_user_text = final_messages[1].text or ""
        self.assertIn("旧上下文摘要", final_user_text)
        self.assertIn("当前新消息", final_user_text)
        self.assertIn("【角色沉浸要求】", final_user_text)
        self.assertEqual(len(chat_handler.messages_lst), 3)
        self.assertEqual(chat_handler.messages_lst[1].text, final_user_text)
        self.assertEqual(chat_handler.messages_lst[2].text, "压缩后回复")
