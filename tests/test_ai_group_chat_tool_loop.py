"""AI 群聊工具循环测试。"""

import tempfile
import unittest
from pathlib import Path
from typing import Protocol, cast

from app.models import GroupMessage, JsonObject, MessageSegment, Response, Sender, Text
from app.plugins.ai_group_chat.ai_group_chat import AIGroupChatPlugin
from app.plugins.ai_group_chat.config import AIGroupChatConfig
from app.plugins.ai_group_chat.debug_dump import AIGroupChatDebugDumper
from app.plugins.ai_group_chat.tool_loop import ActiveModelConfig, GroupChatToolLoop
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
        return LLMResponse(content="工具后回复", reasoning_content="最终思考")


class FakeMultiRoundToolCallLLM:
    """测试用 LLM，连续两轮调用信息工具后再给出最终回复。"""

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
        """前两轮分别调用信息工具，第三轮给出最终正文。"""
        _ = (model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        self.received_messages.append(messages[:])
        if len(self.received_messages) == 1:
            return LLMResponse(
                content="第一次先查一下",
                tool_calls=[
                    LLMToolCall(id="call-1", name="test__lookup", arguments={})
                ],
            )
        if len(self.received_messages) == 2:
            return LLMResponse(
                content="我再补查一下",
                tool_calls=[
                    LLMToolCall(id="call-2", name="test__lookup", arguments={})
                ],
            )
        return LLMResponse(content="两次工具结果都看完了")


class FakeMarkedContentLLM:
    """测试用 LLM，在 content 中输出引用和艾特标记。"""

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
        """返回带 `<Reply>` 和 `<At>` 标记的正文。"""
        _ = (messages, model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        return LLMResponse(content="<Reply>\n<At>20000</At>\n收到喵")


class FakeInvalidDirectiveThenReplyLLM:
    """测试用 LLM，先输出非法标记，收到错误后改写正文。"""

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
        """第一轮使用被禁用的 @全体，第二轮改成合法引用。"""
        _ = (model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        self.received_messages.append(messages[:])
        if len(self.received_messages) == 1:
            return LLMResponse(content="<At>all</At>\n大家看这里")
        return LLMResponse(content="<Reply>\n我在喵~ 已经改成合法标记了！")


class FakeNoToolLLM:
    """测试用 LLM，输出无工具正文后应由插件自动结束。"""

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
        """返回无工具正文并记录请求。"""
        _ = (model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        self.received_messages.append(messages[:])
        return LLMResponse(content="第一句")


class FakeEmptyLLM:
    """测试用 LLM，返回空响应后应由插件静默结束。"""

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
        """返回空响应并记录请求。"""
        _ = (model_vendors, model_name, tools, tool_choice, parallel_tool_calls)
        self.received_messages.append(messages[:])
        return LLMResponse()


class FakeCompressionLLM:
    """测试用 LLM，先压缩上下文，再返回最终回复。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.compression_messages: list[ChatMessage] = []
        self.received_messages: list[list[ChatMessage]] = []
        self.compression_model_calls: list[tuple[str, str]] = []
        self.formal_model_calls: list[tuple[str, str]] = []

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """记录压缩请求并返回固定摘要。"""
        self.compression_model_calls.append((model_vendors, model_name))
        self.compression_messages = messages[:]
        return "历史上下文摘要：用户在讨论上下文压缩。"

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
        _ = (tools, tool_choice, parallel_tool_calls)
        self.formal_model_calls.append((model_vendors, model_name))
        self.received_messages.append(messages[:])
        return LLMResponse(content="压缩后回复")


class FakeRoutingLLM:
    """测试用 LLM，记录正式请求使用的模型。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.received_messages: list[list[ChatMessage]] = []
        self.formal_model_calls: list[tuple[str, str]] = []

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
        """记录正式请求模型并返回正文。"""
        _ = (tools, tool_choice, parallel_tool_calls)
        self.formal_model_calls.append((model_vendors, model_name))
        self.received_messages.append(messages[:])
        return LLMResponse(content="路由回复")


def build_config(
    *,
    output_reasoning_content: bool,
    pass_back_reasoning_content: bool = False,
    model_name: str = "gpt-5.5",
    model_vendors: str = "CLIProxyAPI",
    supports_multimodal: bool = False,
    context_compression_notice: str = "上下文有点长，我先整理一下记忆，稍等我几秒喵~",
) -> AIGroupChatConfig:
    """构造测试用 AI 群聊配置。"""
    return AIGroupChatConfig(
        model_name=model_name,
        model_vendors=model_vendors,
        supports_multimodal=supports_multimodal,
        multimodal_fallback_model_name="gpt-5.5-vision",
        multimodal_fallback_model_vendors="CLIProxyAPI",
        output_reasoning_content=output_reasoning_content,
        pass_back_reasoning_content=pass_back_reasoning_content,
        context_compression_notice=context_compression_notice,
        group_config=[],
    )


def configure_deepseek_v4_prompts(
    *, config: AIGroupChatConfig, prompt_root: Path
) -> None:
    """给测试配置写入 DeepSeek V4 Depth 0 提示词文件。"""
    extra_path = prompt_root / "extra_requirements.md"
    roleplay_path = prompt_root / "roleplay_instruct.md"
    extra_path.write_text("群聊动作偏好：需要引用或艾特时必须说完整的话。", encoding="utf-8")
    roleplay_path.write_text("【角色沉浸要求】保持第一人称内心独白。", encoding="utf-8")
    config.enable_deepseek_v4_roleplay_instruct = True
    config.extra_requirements_path = str(extra_path)
    config.deepseek_v4_roleplay_instruct_path = str(roleplay_path)


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

    def test_plugin_selects_fallback_model_when_image_exists(self) -> None:
        """主模型不支持多模态且本轮有图片时选择备用模型。"""
        plugin = object.__new__(AIGroupChatPlugin)
        plugin.config = build_config(output_reasoning_content=False)

        active_model = plugin.select_active_model(contains_image=True)

        self.assertEqual(active_model.model_name, "gpt-5.5-vision")
        self.assertEqual(active_model.model_vendors, "CLIProxyAPI")
        self.assertTrue(active_model.supports_multimodal)
        self.assertTrue(active_model.used_multimodal_fallback)

    def test_plugin_keeps_main_model_when_no_image_exists(self) -> None:
        """主模型不支持多模态但本轮无图片时仍使用主模型。"""
        plugin = object.__new__(AIGroupChatPlugin)
        plugin.config = build_config(output_reasoning_content=False)

        active_model = plugin.select_active_model(contains_image=False)

        self.assertEqual(active_model.model_name, "gpt-5.5")
        self.assertFalse(active_model.supports_multimodal)
        self.assertFalse(active_model.used_multimodal_fallback)

    def test_plugin_keeps_multimodal_main_model_when_image_exists(self) -> None:
        """主模型支持多模态时不切换备用模型。"""
        plugin = object.__new__(AIGroupChatPlugin)
        plugin.config = build_config(
            output_reasoning_content=False,
            supports_multimodal=True,
        )

        active_model = plugin.select_active_model(contains_image=True)

        self.assertEqual(active_model.model_name, "gpt-5.5")
        self.assertTrue(active_model.supports_multimodal)
        self.assertFalse(active_model.used_multimodal_fallback)

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
        """开启思维链回传时，长期上下文写入结构化字段且正文干净。"""
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

    async def test_content_directives_are_sent_with_message_segments(self) -> None:
        """content 中的 `<Reply>` / `<At>` 会转换成消息段，长期上下文写入原文。"""
        fake_context = FakeContext(llm=FakeMarkedContentLLM())
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

        self.assertEqual(fake_context.bot.sent_texts, [" 收到喵"])
        self.assertEqual(fake_context.bot.sent_segment_types, [["reply", "at", "text"]])
        self.assertEqual(chat_handler.messages_lst[-1].role, "assistant")
        self.assertEqual(
            chat_handler.messages_lst[-1].text,
            "<Reply>\n<At>20000</At>\n收到喵",
        )

    async def test_invalid_content_directive_returns_error_and_retries(self) -> None:
        """非法 content 标记不会发群，会追加纠错消息让模型重试。"""
        fake_llm = FakeInvalidDirectiveThenReplyLLM()
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
            turn_messages=[ChatMessage(role="user", text="还活着?")],
        )

        self.assertEqual(
            fake_context.bot.sent_texts,
            ["我在喵~ 已经改成合法标记了！"],
        )
        self.assertEqual(fake_context.bot.sent_segment_types, [["reply", "text"]])
        self.assertEqual(len(fake_llm.received_messages), 2)
        retry_messages = fake_llm.received_messages[1]
        directive_error_message = next(
            message
            for message in retry_messages
            if message.role == "user" and "标记格式有误" in (message.text or "")
        )
        self.assertIn("@全体", directive_error_message.text or "")
        self.assertEqual(chat_handler.messages_lst[-1].role, "assistant")
        self.assertEqual(
            chat_handler.messages_lst[-1].text,
            "<Reply>\n我在喵~ 已经改成合法标记了！",
        )

    async def test_plain_content_without_tools_sends_once_and_finishes(self) -> None:
        """无工具正文会发群一次，然后自动结束本轮。"""
        fake_llm = FakeNoToolLLM()
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

        self.assertEqual(fake_context.bot.sent_texts, ["第一句"])
        self.assertEqual(len(fake_llm.received_messages), 1)
        self.assertEqual(chat_handler.messages_lst[-1].text, "第一句")

    async def test_empty_content_without_tools_finishes_silently(self) -> None:
        """无工具且无正文时不发送群消息，但当前用户消息仍进入长期上下文。"""
        fake_llm = FakeEmptyLLM()
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
            turn_messages=[ChatMessage(role="user", text="不用回复")],
        )

        self.assertEqual(fake_context.bot.sent_texts, [])
        self.assertEqual(len(fake_llm.received_messages), 1)
        self.assertEqual(
            [message.role for message in chat_handler.messages_lst],
            ["system", "user"],
        )
        self.assertEqual(chat_handler.messages_lst[-1].text, "不用回复")

    async def test_formal_requests_use_active_model(self) -> None:
        """正式请求使用本轮 active model。"""
        fake_llm = FakeRoutingLLM()
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
            turn_messages=[
                ChatMessage(role="user", text="图片消息", image=[b"image-bytes"])
            ],
            active_model=ActiveModelConfig(
                model_name="gpt-5.5-vision",
                model_vendors="vision-vendor",
                supports_multimodal=True,
                used_multimodal_fallback=True,
            ),
        )

        self.assertEqual(fake_llm.formal_model_calls, [("vision-vendor", "gpt-5.5-vision")])
        self.assertEqual(fake_llm.received_messages[0][1].image, [b"image-bytes"])
        self.assertIsNone(chat_handler.messages_lst[1].image)

    async def test_history_images_are_not_replayed_to_next_turn(self) -> None:
        """历史上下文中的图片字节不会跨轮次进入普通模型请求。"""
        fake_llm = FakeRoutingLLM()
        fake_context = FakeContext(llm=fake_llm)
        config = build_config(output_reasoning_content=False)
        tool_loop = GroupChatToolLoop(
            config=config,
            context=cast(Context, fake_context),
            debug_dumper=AIGroupChatDebugDumper(config=config),
        )
        chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)
        chat_handler.build_chatmessage(
            message_lst=[
                ChatMessage(role="user", text="上一轮图片", image=[b"old-image"]),
                ChatMessage(role="assistant", text="上一轮回复"),
            ]
        )

        await tool_loop.run(
            msg=build_message(),
            chat_handler=chat_handler,
            turn_messages=[ChatMessage(role="user", text="这一轮没有图片")],
            active_model=ActiveModelConfig(
                model_name="gpt-5.5",
                model_vendors="text-vendor",
                supports_multimodal=False,
            ),
        )

        first_request_messages = fake_llm.received_messages[0]
        self.assertEqual(fake_llm.formal_model_calls, [("text-vendor", "gpt-5.5")])
        self.assertEqual(
            [len(message.image or []) for message in first_request_messages],
            [0, 0, 0, 0],
        )
        self.assertEqual(chat_handler.messages_lst[1].text, "上一轮图片")
        self.assertIsNone(chat_handler.messages_lst[1].image)

    async def test_deepseek_v4_depth_zero_prompt_is_added_to_each_formal_request(
        self,
    ) -> None:
        """DeepSeek V4 正式请求每次都追加 Depth 0 user prompt。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_llm = FakeToolCallLLM()
            fake_context = FakeContext(
                llm=fake_llm,
                mcp_tool_manager=FakeInfoToolManager(),
            )
            config = build_config(
                output_reasoning_content=False,
                model_name="deepseek-v4-pro",
            )
            configure_deepseek_v4_prompts(
                config=config,
                prompt_root=Path(temp_dir),
            )
            tool_loop = GroupChatToolLoop(
                config=config,
                context=cast(Context, fake_context),
                debug_dumper=AIGroupChatDebugDumper(config=config),
            )
            chat_handler = ContextHandler(
                system_prompt="系统提示词",
                max_context_tokens=1000000,
            )

            await tool_loop.run(
                msg=build_message(),
                chat_handler=chat_handler,
                turn_messages=[ChatMessage(role="user", text="用户消息")],
            )

            self.assertEqual(len(fake_llm.received_messages), 2)
            for request_messages in fake_llm.received_messages:
                depth_zero_message = request_messages[-1]
                depth_zero_text = depth_zero_message.text or ""
                self.assertEqual(depth_zero_message.role, "user")
                self.assertIn("<其他需求>", depth_zero_text)
                self.assertIn("<角色沉浸式扮演需求>", depth_zero_text)
            first_real_user_text = fake_llm.received_messages[0][1].text or ""
            self.assertNotIn("<角色沉浸式扮演需求>", first_real_user_text)
            persisted_text = "\n".join(
                message.text or "" for message in chat_handler.messages_lst
            )
            self.assertNotIn("<其他需求>", persisted_text)
            self.assertNotIn("<角色沉浸式扮演需求>", persisted_text)

    async def test_deepseek_v4_depth_zero_prompt_follows_accumulated_tool_results(
        self,
    ) -> None:
        """多轮信息工具续问时，每次请求都以累积工具结果加 Depth 0 结尾。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_llm = FakeMultiRoundToolCallLLM()
            fake_context = FakeContext(
                llm=fake_llm,
                mcp_tool_manager=FakeInfoToolManager(),
            )
            config = build_config(
                output_reasoning_content=False,
                model_name="deepseek-v4-pro",
            )
            configure_deepseek_v4_prompts(
                config=config,
                prompt_root=Path(temp_dir),
            )
            tool_loop = GroupChatToolLoop(
                config=config,
                context=cast(Context, fake_context),
                debug_dumper=AIGroupChatDebugDumper(config=config),
            )
            chat_handler = ContextHandler(
                system_prompt="系统提示词",
                max_context_tokens=1000000,
            )

            await tool_loop.run(
                msg=build_message(),
                chat_handler=chat_handler,
                turn_messages=[ChatMessage(role="user", text="连续查两次")],
            )

            self.assertEqual(len(fake_llm.received_messages), 3)
            first_request = fake_llm.received_messages[0]
            second_request = fake_llm.received_messages[1]
            third_request = fake_llm.received_messages[2]
            self.assertEqual([message.role for message in first_request], ["system", "user", "user"])
            self.assertEqual(
                [message.role for message in second_request],
                ["system", "user", "assistant", "tool", "user"],
            )
            self.assertEqual(
                [message.role for message in third_request],
                ["system", "user", "assistant", "tool", "assistant", "tool", "user"],
            )
            self.assertEqual(second_request[-2].tool_call_id, "call-1")
            self.assertIn("<角色沉浸式扮演需求>", second_request[-1].text or "")
            self.assertEqual(third_request[-4].tool_call_id, "call-1")
            self.assertEqual(third_request[-2].tool_call_id, "call-2")
            self.assertIn("<角色沉浸式扮演需求>", third_request[-1].text or "")

    async def test_active_deepseek_v4_prompt_skips_extra_when_system_has_it(
        self,
    ) -> None:
        """备用 DeepSeek V4 请求在 system 已含通用要求时只注入角色沉浸提示。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_llm = FakeRoutingLLM()
            fake_context = FakeContext(llm=fake_llm)
            config = build_config(
                output_reasoning_content=False,
                model_name="gpt-5.5",
            )
            configure_deepseek_v4_prompts(
                config=config,
                prompt_root=Path(temp_dir),
            )
            tool_loop = GroupChatToolLoop(
                config=config,
                context=cast(Context, fake_context),
                debug_dumper=AIGroupChatDebugDumper(config=config),
            )
            chat_handler = ContextHandler(
                system_prompt="系统提示词\n群聊动作偏好：需要引用或艾特时必须说完整的话。",
                max_context_tokens=1000000,
            )

            await tool_loop.run(
                msg=build_message(),
                chat_handler=chat_handler,
                turn_messages=[ChatMessage(role="user", text="用户消息")],
                active_model=ActiveModelConfig(
                    model_name="deepseek-v4-pro",
                    model_vendors="deepseek",
                    supports_multimodal=True,
                    used_multimodal_fallback=True,
                ),
            )

            prompt_text = fake_llm.received_messages[0][-1].text or ""
            self.assertNotIn("<其他需求>", prompt_text)
            self.assertIn("<角色沉浸式扮演需求>", prompt_text)
            self.assertIn("【角色沉浸要求】", prompt_text)

    async def test_context_compression_excludes_current_message_then_rebuilds(
        self,
    ) -> None:
        """上下文超预算时，压缩请求不注入 Depth 0，正式请求才注入。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_llm = FakeCompressionLLM()
            fake_context = FakeContext(llm=fake_llm)
            config = build_config(
                output_reasoning_content=False,
                model_name="deepseek-v4-pro",
                context_compression_notice="我先整理一下记忆喵~",
            )
            configure_deepseek_v4_prompts(
                config=config,
                prompt_root=Path(temp_dir),
            )
            tool_loop = GroupChatToolLoop(
                config=config,
                context=cast(Context, fake_context),
                debug_dumper=AIGroupChatDebugDumper(config=config),
            )
            chat_handler = ContextHandler(
                system_prompt="系统提示词",
                max_context_tokens=6000,
            )
            chat_handler.build_chatmessage(
                message_lst=[
                    ChatMessage(role="user", text="历史消息一" * 100),
                    ChatMessage(role="assistant", text="历史回复一" * 100),
                ]
            )

            await tool_loop.run(
                msg=build_message(),
                chat_handler=chat_handler,
                turn_messages=[ChatMessage(role="user", text="当前新消息")],
            )

            compression_user_text = fake_llm.compression_messages[1].text or ""
            self.assertIn("历史消息一", compression_user_text)
            self.assertNotIn("当前新消息", compression_user_text)
            self.assertNotIn("<角色沉浸式扮演需求>", compression_user_text)
            self.assertEqual(
                fake_context.bot.sent_texts,
                ["我先整理一下记忆喵~", "压缩后回复"],
            )
            final_messages = fake_llm.received_messages[0]
            self.assertEqual(
                [message.role for message in final_messages],
                ["system", "user", "user"],
            )
            rebuilt_user_text = final_messages[1].text or ""
            depth_zero_text = final_messages[2].text or ""
            self.assertIn("历史上下文摘要", rebuilt_user_text)
            self.assertIn("当前新消息", rebuilt_user_text)
            self.assertNotIn("【角色沉浸要求】", rebuilt_user_text)
            self.assertIn("<其他需求>", depth_zero_text)
            self.assertIn("<角色沉浸式扮演需求>", depth_zero_text)
            self.assertEqual(len(chat_handler.messages_lst), 3)
            self.assertEqual(chat_handler.messages_lst[1].text, rebuilt_user_text)
            self.assertEqual(chat_handler.messages_lst[2].text, "压缩后回复")

    async def test_context_compression_uses_main_model_before_active_model(
        self,
    ) -> None:
        """上下文压缩使用主模型，压缩后的正式请求使用 active model。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_llm = FakeCompressionLLM()
            fake_context = FakeContext(llm=fake_llm)
            config = build_config(
                output_reasoning_content=False,
                model_name="deepseek-v4-pro",
                model_vendors="main-vendor",
                context_compression_notice="我先整理一下记忆喵~",
            )
            configure_deepseek_v4_prompts(
                config=config,
                prompt_root=Path(temp_dir),
            )
            tool_loop = GroupChatToolLoop(
                config=config,
                context=cast(Context, fake_context),
                debug_dumper=AIGroupChatDebugDumper(config=config),
            )
            chat_handler = ContextHandler(
                system_prompt="系统提示词",
                max_context_tokens=8000,
            )
            chat_handler.build_chatmessage(
                message_lst=[
                    ChatMessage(role="user", text="历史消息一" * 100),
                    ChatMessage(role="assistant", text="历史回复一" * 100),
                ]
            )

            await tool_loop.run(
                msg=build_message(),
                chat_handler=chat_handler,
                turn_messages=[
                    ChatMessage(role="user", text="当前图片", image=[b"image-bytes"])
                ],
                active_model=ActiveModelConfig(
                    model_name="gpt-5.5-vision",
                    model_vendors="vision-vendor",
                    supports_multimodal=True,
                    used_multimodal_fallback=True,
                ),
            )

            self.assertEqual(
                fake_llm.compression_model_calls,
                [("main-vendor", "deepseek-v4-pro")],
            )
            self.assertEqual(
                fake_llm.formal_model_calls,
                [("vision-vendor", "gpt-5.5-vision")],
            )
            final_messages = fake_llm.received_messages[0]
            self.assertEqual(final_messages[1].image, [b"image-bytes"])
            self.assertIn("<其他需求>", final_messages[-1].text or "")
            self.assertNotIn("<角色沉浸式扮演需求>", final_messages[-1].text or "")
