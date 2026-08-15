"""AI 群聊工具循环与视觉工具集成测试。"""

import unittest
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx

from app.api.mixins.message import NapCatSendMessageError
from app.database import GroupDataScope, StoredGroupMessage
from app.models import (
    Forward,
    GroupMessage,
    JsonObject,
    MessageSegment,
    Node,
    Response,
    Sender,
    Text,
)
from app.plugins.ai_group_chat.config import AIGroupChatConfig
from app.plugins.ai_group_chat.debug_dump import AIGroupChatDebugDumper
from app.plugins.ai_group_chat.tool_loop import GroupChatToolLoop
from app.plugins.ai_group_chat.vision_tool import (
    VisionDescriptionTool,
    VisionTurnState,
)
from app.plugins.base import Context
from app.services import ChatMessage, ContextHandler
from app.services.llm.schemas import (
    LLMResponse,
    LLMToolCall,
    LLMToolChoice,
    LLMToolDefinition,
)
from app.services.llm.tools import (
    LLMImageArtifact,
    LLMImageError,
    LLMToolExecutionResult,
)

VISION_SYSTEM_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/system.md"
VISION_USER_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/user.md"
TOOL_NAME = "mcp__fake__inspect"


class FakeLLMProtocol(Protocol):
    """描述工具循环测试所需的 LLM 接口。"""

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """返回纯文本响应。"""
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
        """返回带可选工具调用的结构化响应。"""
        ...


class RecordingLLM:
    """按队列返回正式响应，并记录正式请求和独立文本请求。"""

    def __init__(
        self,
        *,
        responses: list[LLMResponse],
        text_response: str = "画面里有白底黑字。",
    ) -> None:
        """保存响应队列。"""
        self.responses: list[LLMResponse] = responses
        self.text_response: str = text_response
        self.formal_requests: list[list[ChatMessage]] = []
        self.formal_models: list[tuple[str, str]] = []
        self.text_requests: list[list[ChatMessage]] = []
        self.text_models: list[tuple[str, str]] = []

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
    ) -> str:
        """记录视觉或压缩请求并返回固定文本。"""
        self.text_requests.append(messages[:])
        self.text_models.append((model_vendors, model_name))
        return self.text_response

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """记录正式请求并弹出下一条响应。"""
        _ = (tools, tool_choice, parallel_tool_calls)
        self.formal_requests.append(messages[:])
        self.formal_models.append((model_vendors, model_name))
        if not self.responses:
            raise AssertionError("正式响应队列已耗尽")
        return self.responses.pop(0)


class FakeToolManager:
    """暴露一个可返回内部图片附件的信息工具。"""

    def __init__(
        self,
        *,
        image_bytes: bytes | None = None,
        image_errors: list[LLMImageError] | None = None,
        truncated_image_count: int = 0,
    ) -> None:
        """保存可选图片内容。"""
        self.image_bytes: bytes | None = image_bytes
        self.image_errors: list[LLMImageError] = image_errors or []
        self.truncated_image_count: int = truncated_image_count
        self.calls: list[tuple[str, JsonObject]] = []

    def list_tools(self) -> list[LLMToolDefinition]:
        """返回固定工具定义。"""
        return [
            LLMToolDefinition(
                name=TOOL_NAME,
                description="读取测试信息。",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            )
        ]

    async def call_tool_with_artifacts(
        self, name: str, arguments: JsonObject
    ) -> LLMToolExecutionResult:
        """返回 JSON 结果和可选图片附件。"""
        self.calls.append((name, arguments))
        artifacts = (
            [
                LLMImageArtifact(
                    label="工具图片 1",
                    image_bytes=self.image_bytes,
                )
            ]
            if self.image_bytes is not None
            else []
        )
        return LLMToolExecutionResult(
            result={"ok": True, "value": "工具结果"},
            image_items=[*artifacts, *self.image_errors],
            truncated_image_count=self.truncated_image_count,
        )


class EmptyGroupMessageReader:
    """测试中不实际读取历史消息。"""

    def __init__(self) -> None:
        """初始化当前群未撤回消息映射。"""
        self.active_messages: dict[tuple[str, str, str], StoredGroupMessage] = {}

    def add_forward(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        forward_id: str,
    ) -> None:
        """加入一条含单个顶层合并转发段的未撤回群消息。"""
        self.active_messages[(scope.bot_id, scope.group_id, message_id)] = (
            StoredGroupMessage(
                row_id=1,
                scope=scope,
                message_id=message_id,
                group_name="测试群",
                sender_id="20000",
                sender_name="夜袭",
                sender_role="member",
                occurred_at=datetime(2026, 8, 16, tzinfo=UTC),
                direction="incoming",
                segments=(Forward.new(forward_id),),
                images=(),
            )
        )

    async def get_active(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
    ) -> StoredGroupMessage | None:
        """只返回当前机器人和当前群显式加入的未撤回消息。"""
        return self.active_messages.get((scope.bot_id, scope.group_id, message_id))


class FakeBot:
    """记录发出的群文本和合并转发。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.boot_id = "10000"
        self.sent_texts: list[str] = []
        self.sent_segment_types: list[list[str]] = []
        self.sent_forwards: list[tuple[str, list[MessageSegment]]] = []
        self.forward_responses: dict[str, Response] = {}
        self.image_responses: dict[str, Response] = {}
        self.forward_calls: list[str] = []
        self.image_calls: list[tuple[str | None, str | None]] = []

    async def send_msg(
        self,
        *,
        group_id: str,
        text: str | None = None,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """记录普通消息。"""
        _ = group_id
        if text is not None:
            self.sent_texts.append(text)
        if message_segment is not None:
            self.sent_segment_types.append(
                [segment.type for segment in message_segment]
            )
            self.sent_texts.append(
                "".join(
                    segment.data.text
                    for segment in message_segment
                    if isinstance(segment, Text)
                )
            )
        return Response(status="ok", retcode=0)

    async def send_group_forward_msg(
        self, *, group_id: str, messages: list[MessageSegment]
    ) -> Response:
        """记录合并转发消息。"""
        self.sent_forwards.append((group_id, messages))
        return Response(status="ok", retcode=0)

    async def get_forward_msg(self, *, message_id: str) -> Response:
        """返回预置的合并转发响应。"""
        self.forward_calls.append(message_id)
        return self.forward_responses.get(
            message_id,
            Response(status="failed", retcode=404, message="合并转发不存在"),
        )

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """返回预置的图片刷新响应。"""
        self.image_calls.append((file_id, file))
        key = file if file is not None else file_id
        if key is not None and key in self.image_responses:
            return self.image_responses[key]
        return Response(status="failed", retcode=404, message="图片不存在")


class FakeSendFailureBot(FakeBot):
    """模拟 NapCat 发送层最终失败。"""

    async def send_msg(
        self,
        *,
        group_id: str,
        text: str | None = None,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """抛出发送层显式异常。"""
        _ = (group_id, text, message_segment)
        raise NapCatSendMessageError("NapCat 发送消息失败: send timeout")


class FakeContext:
    """只提供工具循环实际消费的上下文成员。"""

    def __init__(
        self,
        *,
        llm: RecordingLLM,
        tool_manager: FakeToolManager | None = None,
    ) -> None:
        """保存测试依赖。"""
        self.bot = FakeBot()
        self.group_messages = EmptyGroupMessageReader()
        self.direct_httpx = cast(httpx.AsyncClient, object())
        self.llm: FakeLLMProtocol = llm
        self.mcp_tool_manager = (
            tool_manager if tool_manager is not None else FakeToolManager()
        )


def build_config(
    *,
    supports_multimodal: bool = False,
    model_name: str = "main-model",
    model_vendors: str = "main-vendor",
    output_reasoning_content: bool = False,
    pass_back_reasoning_content: bool = False,
    persist_vision_descriptions: bool = True,
    max_reply_chars: int = 100,
    context_compression_notice: str = "正在整理上下文",
) -> AIGroupChatConfig:
    """按主模型能力构造有效配置。"""
    values: dict[str, object] = {
        "model_name": model_name,
        "model_vendors": model_vendors,
        "supports_multimodal": supports_multimodal,
        "output_reasoning_content": output_reasoning_content,
        "pass_back_reasoning_content": pass_back_reasoning_content,
        "persist_vision_descriptions": persist_vision_descriptions,
        "max_reply_chars": max_reply_chars,
        "context_compression_notice": context_compression_notice,
        "group_config": [],
    }
    if not supports_multimodal:
        values.update(
            {
                "vision_model_name": "vision-model",
                "vision_model_vendors": "vision-vendor",
                "vision_system_prompt_path": VISION_SYSTEM_PROMPT_PATH,
                "vision_user_prompt_path": VISION_USER_PROMPT_PATH,
            }
        )
    return AIGroupChatConfig.model_validate(values)


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


def build_tool_call(call_id: str = "call-1") -> LLMToolCall:
    """构造固定信息工具调用。"""
    return LLMToolCall(id=call_id, name=TOOL_NAME, arguments={})


def build_loop(
    *, config: AIGroupChatConfig, context: FakeContext
) -> GroupChatToolLoop:
    """构造使用真实视觉工具的工具循环。"""
    typed_context = cast(Context, context)
    return GroupChatToolLoop(
        config=config,
        context=typed_context,
        debug_dumper=AIGroupChatDebugDumper(config=config),
        vision_tool=VisionDescriptionTool(config=config, context=typed_context),
    )


async def run_turn(
    *,
    loop: GroupChatToolLoop,
    chat_handler: ContextHandler,
    question: str = "请根据图片回答",
    turn_messages: list[ChatMessage] | None = None,
    input_vision_messages: list[ChatMessage] | None = None,
    input_vision_history_messages: list[ChatMessage] | None = None,
    vision_turn_state: VisionTurnState | None = None,
) -> None:
    """用空的输入视觉结果执行一轮工具循环。"""
    await loop.run(
        msg=build_message(),
        chat_handler=chat_handler,
        turn_messages=(
            turn_messages
            if turn_messages is not None
            else [ChatMessage(role="user", text=question)]
        ),
        input_vision_messages=input_vision_messages or [],
        input_vision_history_messages=input_vision_history_messages or [],
        question=question,
        vision_turn_state=(
            vision_turn_state if vision_turn_state is not None else VisionTurnState()
        ),
    )


class GroupChatToolLoopTest(unittest.IsolatedAsyncioTestCase):
    """验证主模型路由、视觉隔离、思维字段和上下文保存。"""

    async def test_reasoning_is_hidden_but_can_be_passed_back(self) -> None:
        """群内只显示 content，结构化 reasoning 可写回后续上下文。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(content="正式回复", reasoning_content="模型思考")
            ]
        )
        context = FakeContext(llm=llm)
        config = build_config(pass_back_reasoning_content=True)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        self.assertEqual(context.bot.sent_texts, ["正式回复"])
        self.assertEqual(chat_handler.messages_lst[-1].text, "正式回复")
        self.assertEqual(
            chat_handler.messages_lst[-1].reasoning_content,
            "模型思考",
        )

    async def test_reasoning_output_option_only_changes_visible_reply(self) -> None:
        """开启展示能力时群内可见思维内容，长期正文仍保持干净。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(content="正式回复", reasoning_content="模型思考")
            ]
        )
        context = FakeContext(llm=llm)
        config = build_config(output_reasoning_content=True)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        self.assertEqual(
            context.bot.sent_texts,
            [
                "【模型原生思维链】\n"
                "---\n"
                "模型思考\n"
                "---\n\n"
                "【回复】\n"
                "正式回复"
            ],
        )
        self.assertEqual(chat_handler.messages_lst[-1].text, "正式回复")
        self.assertIsNone(chat_handler.messages_lst[-1].reasoning_content)

    async def test_input_image_description_is_used_before_main_request(self) -> None:
        """当前消息图片先生成描述，再由固定主模型完成正式回复。"""
        llm = RecordingLLM(responses=[LLMResponse(content="最终回复")])
        context = FakeContext(llm=llm)
        config = build_config()
        typed_context = cast(Context, context)
        vision_tool = VisionDescriptionTool(
            config=config,
            context=typed_context,
        )
        state = VisionTurnState()
        delivery = await vision_tool.deliver(
            items=[
                LLMImageArtifact(
                    label="当前消息第 1 张图片",
                    image_bytes=b"current-image",
                )
            ],
            truncated_count=0,
            question="图片里写了什么？",
            source_name="当前消息和引用消息",
            turn_state=state,
        )
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="图片里写了什么？",
            input_vision_messages=delivery.working_messages,
            input_vision_history_messages=delivery.history_messages,
            vision_turn_state=state,
        )

        self.assertEqual(llm.text_models, [("vision-vendor", "vision-model")])
        self.assertEqual(llm.formal_models, [("main-vendor", "main-model")])
        formal_text = "\n".join(
            message.text or "" for message in llm.formal_requests[0]
        )
        self.assertIn("系统生成，不是用户原话", formal_text)
        self.assertIn("画面里有白底黑字", formal_text)
        self.assertTrue(
            all(message.image is None for message in chat_handler.messages_lst)
        )

    async def test_tool_round_passes_back_reasoning_field(self) -> None:
        """工具续问会带回上一轮 assistant 的结构化 reasoning。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(
                    content="我先查一下",
                    reasoning_content="工具前思考",
                    tool_calls=[build_tool_call()],
                ),
                LLMResponse(content="工具后回复"),
            ]
        )
        context = FakeContext(llm=llm)
        config = build_config(pass_back_reasoning_content=True)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        assistant_tool_message = next(
            message for message in llm.formal_requests[1] if message.tool_calls
        )
        self.assertEqual(assistant_tool_message.reasoning_content, "工具前思考")
        self.assertEqual(context.bot.sent_texts, ["我先查一下", "工具后回复"])

    async def test_deepseek_named_model_gets_no_temporary_prompt(self) -> None:
        """模型名称不再触发 DSV4 临时 user 消息。"""
        llm = RecordingLLM(responses=[LLMResponse(content="正常回复")])
        context = FakeContext(llm=llm)
        config = build_config(
            model_name="deepseek-v4-pro",
            model_vendors="deepseek",
        )
        chat_handler = ContextHandler(
            system_prompt="系统提示词\n通用群聊要求",
            max_context_tokens=1000000,
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="用户消息",
        )

        self.assertEqual(
            [message.role for message in llm.formal_requests[0]],
            ["system", "user"],
        )
        self.assertEqual(
            llm.formal_models,
            [("deepseek", "deepseek-v4-pro")],
        )

    async def test_tool_image_uses_isolated_vision_model_then_main_model(self) -> None:
        """文本主模型只收到视觉描述，正式回复两轮都使用主模型。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(tool_calls=[build_tool_call()]),
                LLMResponse(content="看完图片了"),
            ]
        )
        manager = FakeToolManager(image_bytes=b"tool-image")
        context = FakeContext(llm=llm, tool_manager=manager)
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="群聊角色与长期历史不能进入视觉请求",
            max_context_tokens=1000000,
        )
        chat_handler.build_chatmessage(
            message_lst=[ChatMessage(role="assistant", text="长期历史")]
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="图片里的文字是什么？",
        )

        self.assertEqual(
            llm.formal_models,
            [("main-vendor", "main-model"), ("main-vendor", "main-model")],
        )
        self.assertEqual(llm.text_models, [("vision-vendor", "vision-model")])
        self.assertEqual(len(llm.text_requests), 1)
        vision_messages = llm.text_requests[0]
        self.assertEqual([message.role for message in vision_messages], ["system", "user"])
        vision_text = "\n".join(message.text or "" for message in vision_messages)
        self.assertIn("图片里的文字是什么？", vision_text)
        self.assertNotIn("群聊角色", vision_text)
        self.assertNotIn("长期历史", vision_text)
        self.assertEqual(vision_messages[1].image, [b"tool-image"])
        second_text = "\n".join(
            message.text or "" for message in llm.formal_requests[1]
        )
        self.assertIn("系统生成，不是用户原话", second_text)
        self.assertIn("画面里有白底黑字", second_text)
        persisted_text = "\n".join(
            message.text or "" for message in chat_handler.messages_lst
        )
        self.assertIn("画面里有白底黑字", persisted_text)
        persisted_texts = [message.text or "" for message in chat_handler.messages_lst]
        self.assertLess(
            next(
                index
                for index, text in enumerate(persisted_texts)
                if "画面里有白底黑字" in text
            ),
            persisted_texts.index("看完图片了"),
        )
        self.assertTrue(
            all(message.image is None for message in chat_handler.messages_lst)
        )

    async def test_vision_description_can_be_excluded_from_history(self) -> None:
        """关闭持久化后，视觉描述只参与当前工具轮次。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(tool_calls=[build_tool_call()]),
                LLMResponse(content="本轮回答"),
            ]
        )
        context = FakeContext(
            llm=llm,
            tool_manager=FakeToolManager(image_bytes=b"tool-image"),
        )
        config = build_config(persist_vision_descriptions=False)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        persisted_text = "\n".join(
            message.text or "" for message in chat_handler.messages_lst
        )
        self.assertNotIn("画面里有白底黑字", persisted_text)
        self.assertIn("画面里有白底黑字", "\n".join(
            message.text or "" for message in llm.formal_requests[1]
        ))

    async def test_multimodal_main_model_receives_tool_image_directly(self) -> None:
        """多模态主模型直接收到工具图片，不发起独立视觉请求。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(tool_calls=[build_tool_call()]),
                LLMResponse(content="直接看完图片"),
            ]
        )
        context = FakeContext(
            llm=llm,
            tool_manager=FakeToolManager(image_bytes=b"tool-image"),
        )
        config = build_config(supports_multimodal=True)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        self.assertEqual(llm.text_requests, [])
        image_messages = [
            message
            for message in llm.formal_requests[1]
            if message.image is not None
        ]
        self.assertEqual(len(image_messages), 1)
        self.assertEqual(image_messages[0].image, [b"tool-image"])
        self.assertTrue(
            all(message.image is None for message in chat_handler.messages_lst)
        )

    async def test_tool_image_failures_are_returned_to_main_model(self) -> None:
        """工具图片全部失败或截断时，主模型仍收到结构化可恢复观察。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(tool_calls=[build_tool_call()]),
                LLMResponse(content="根据文字继续回答"),
            ]
        )
        context = FakeContext(
            llm=llm,
            tool_manager=FakeToolManager(
                image_errors=[
                    LLMImageError(
                        label="工具图片 1",
                        error_type="ReadTimeout",
                        error="下载超时",
                    )
                ],
                truncated_image_count=2,
            ),
        )
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        self.assertEqual(llm.text_requests, [])
        second_text = "\n".join(
            message.text or "" for message in llm.formal_requests[1]
        )
        self.assertIn("图片内容不可用", second_text)
        self.assertIn("下载超时", second_text)
        self.assertIn("未观察图片数：2", second_text)

    async def test_repeated_tool_image_is_observed_once_per_turn(self) -> None:
        """同一问题下重复返回相同图片时不重复请求视觉模型。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(tool_calls=[build_tool_call("call-1")]),
                LLMResponse(tool_calls=[build_tool_call("call-2")]),
                LLMResponse(content="最终回答"),
            ]
        )
        context = FakeContext(
            llm=llm,
            tool_manager=FakeToolManager(image_bytes=b"same-image"),
        )
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="这是什么？",
        )

        self.assertEqual(len(llm.text_requests), 1)
        final_observations = [
            message
            for message in llm.formal_requests[-1]
            if "视觉工具观察结果" in (message.text or "")
        ]
        self.assertEqual(len(final_observations), 1)

    async def test_forward_tool_auto_fetches_images_for_text_model(self) -> None:
        """只读取合并转发时会自动补取其中图片并生成视觉描述。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(
                    tool_calls=[
                        LLMToolCall(
                            id="call-forward",
                            name="qq__get_forward_message",
                            arguments={"message_id": "outer-forward"},
                        )
                    ]
                ),
                LLMResponse(content="看完合并转发了"),
            ]
        )
        context = FakeContext(llm=llm)
        context.group_messages.add_forward(
            scope=GroupDataScope(bot_id="10000", group_id="40000"),
            message_id="outer-forward",
            forward_id="root-forward",
        )
        context.bot.forward_responses["root-forward"] = Response(
            status="ok",
            retcode=0,
            data={
                "messages": [
                    {
                        "sender": {"nickname": "小明"},
                        "message": [
                            {"type": "text", "data": {"text": "看图"}},
                            {
                                "type": "image",
                                "data": {
                                    "file": "a.jpg",
                                    "file_id": "img-a",
                                    "summary": "[图片A]",
                                },
                            },
                        ],
                    }
                ]
            },
        )
        context.bot.image_responses["a.jpg"] = Response(
            status="ok",
            retcode=0,
            data={"base64": "dG9vbC1pbWFnZQ=="},
        )
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="评价这个合并转发",
        )

        self.assertEqual(
            context.bot.forward_calls,
            ["root-forward", "root-forward"],
        )
        self.assertEqual(context.bot.image_calls, [(None, "a.jpg")])
        self.assertEqual(llm.text_models, [("vision-vendor", "vision-model")])
        self.assertEqual(llm.text_requests[0][1].image, [b"tool-image"])
        second_request_text = "\n".join(
            message.text or "" for message in llm.formal_requests[1]
        )
        self.assertIn("画面里有白底黑字", second_request_text)
        self.assertEqual(
            llm.formal_models,
            [("main-vendor", "main-model"), ("main-vendor", "main-model")],
        )

    async def test_content_directives_are_sent_as_message_segments(self) -> None:
        """content 中的引用与艾特标记会转换为 NapCat 消息段。"""
        content = "<Reply>\n<At>20000</At>\n收到喵"
        llm = RecordingLLM(responses=[LLMResponse(content=content)])
        context = FakeContext(llm=llm)
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="回复我",
        )

        self.assertEqual(context.bot.sent_segment_types, [["reply", "at", "text"]])
        self.assertIn("收到喵", context.bot.sent_texts[0])
        self.assertEqual(chat_handler.messages_lst[-1].text, content)

    async def test_invalid_content_directive_is_corrected_before_sending(
        self,
    ) -> None:
        """非法 content 标记不会发送，并会要求主模型重写。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(content="<At>all</At>\n大家好"),
                LLMResponse(content="<Reply>\n我在喵~ 已改成合法标记。"),
            ]
        )
        context = FakeContext(llm=llm)
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="还活着吗？",
        )

        self.assertEqual(context.bot.sent_segment_types, [["reply", "text"]])
        self.assertEqual(len(llm.formal_requests), 2)
        retry_text = "\n".join(
            message.text or "" for message in llm.formal_requests[1]
        )
        self.assertIn("标记格式有误", retry_text)
        self.assertIn("@全体", retry_text)
        self.assertEqual(
            chat_handler.messages_lst[-1].text,
            "<Reply>\n我在喵~ 已改成合法标记。",
        )

    async def test_plain_content_sends_once_and_finishes(self) -> None:
        """无工具正文只发送一次并结束本轮。"""
        llm = RecordingLLM(responses=[LLMResponse(content="第一句")])
        context = FakeContext(llm=llm)
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="继续说",
        )

        self.assertEqual(context.bot.sent_texts, ["第一句"])
        self.assertEqual(len(llm.formal_requests), 1)
        self.assertEqual(chat_handler.messages_lst[-1].text, "第一句")

    async def test_send_failure_persists_status_without_assistant_content(
        self,
    ) -> None:
        """发送失败时只记录运行状态，不伪装成已发出的 assistant。"""
        llm = RecordingLLM(responses=[LLMResponse(content="第一句")])
        context = FakeContext(llm=llm)
        context.bot = FakeSendFailureBot()
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="继续说",
        )

        self.assertEqual(
            [message.role for message in chat_handler.messages_lst],
            ["system", "user", "system"],
        )
        failure_status = chat_handler.messages_lst[-1].text or ""
        self.assertIn("没有发送到群内", failure_status)
        self.assertIn("NapCat 发送消息失败", failure_status)
        self.assertNotIn(
            "第一句",
            [message.text for message in chat_handler.messages_lst],
        )

    async def test_empty_content_without_tools_finishes_silently(self) -> None:
        """没有正文或工具时不发送消息，但仍保存本轮用户输入。"""
        llm = RecordingLLM(responses=[LLMResponse(content="  ")])
        context = FakeContext(llm=llm)
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="不用回复",
        )

        self.assertEqual(context.bot.sent_texts, [])
        self.assertEqual(
            [message.role for message in chat_handler.messages_lst],
            ["system", "user"],
        )
        self.assertEqual(chat_handler.messages_lst[-1].text, "不用回复")

    async def test_history_image_bytes_are_never_replayed(self) -> None:
        """历史图片字节会在正式请求前清理，也不会再次持久化。"""
        llm = RecordingLLM(responses=[LLMResponse(content="下一轮回复")])
        context = FakeContext(llm=llm)
        config = build_config()
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )
        chat_handler.build_chatmessage(
            message_lst=[
                ChatMessage(role="user", text="上一轮图片", image=[b"old-image"]),
                ChatMessage(role="assistant", text="上一轮回复"),
            ]
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="这一轮没有图片",
        )

        self.assertTrue(
            all(
                message.image is None
                for message in llm.formal_requests[0]
            )
        )
        self.assertTrue(
            all(message.image is None for message in chat_handler.messages_lst)
        )

    async def test_context_compression_and_formal_reply_use_main_model(self) -> None:
        """上下文压缩与压缩后的正式请求都固定使用主模型。"""
        llm = RecordingLLM(
            responses=[LLMResponse(content="压缩后回复")],
            text_response="历史上下文摘要",
        )
        context = FakeContext(llm=llm)
        config = build_config(
            model_name="deepseek-v4-pro",
            model_vendors="main-vendor",
            context_compression_notice="我先整理一下记忆",
        )
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=9000
        )
        chat_handler.build_chatmessage(
            message_lst=[
                ChatMessage(role="user", text="历史消息一" * 100),
                ChatMessage(role="assistant", text="历史回复一" * 100),
            ]
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="当前新消息",
        )

        self.assertEqual(llm.text_models, [("main-vendor", "deepseek-v4-pro")])
        self.assertEqual(llm.formal_models, [("main-vendor", "deepseek-v4-pro")])
        compression_text = llm.text_requests[0][1].text or ""
        self.assertIn("历史消息一", compression_text)
        self.assertNotIn("当前新消息", compression_text)
        self.assertEqual(
            [message.role for message in llm.formal_requests[0]],
            ["system", "user"],
        )
        rebuilt_text = llm.formal_requests[0][1].text or ""
        self.assertIn("历史上下文摘要", rebuilt_text)
        self.assertIn("当前新消息", rebuilt_text)
        self.assertEqual(
            context.bot.sent_texts,
            ["我先整理一下记忆", "压缩后回复"],
        )

    async def test_long_reply_still_uses_group_forward_sender(self) -> None:
        """超过普通发送阈值的 content 仍通过单节点合并转发发送。"""
        llm = RecordingLLM(
            responses=[LLMResponse(content="这是一段很长的正式回复")]
        )
        context = FakeContext(llm=llm)
        config = build_config(max_reply_chars=5)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        self.assertEqual(context.bot.sent_texts, [])
        self.assertEqual(len(context.bot.sent_forwards), 1)
        _, segments = context.bot.sent_forwards[0]
        self.assertIsInstance(segments[0], Node)


if __name__ == "__main__":
    unittest.main()
