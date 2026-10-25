"""AI 群聊工具循环与图片交付集成测试。"""

import unittest
from unittest.mock import patch
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx

from app.api.mixins.message import NapCatSendMessageError
from app.config import AIGroupChatConfig
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
from app.plugins.ai_group_chat.tool_loop import GroupChatToolLoop, GroupChatTurnError
from app.services.llm.errors import LLMRequestError
from app.plugins.ai_group_chat.image_delivery import (
    ImageDeliveryService,
    ImageTurnState,
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
from tests.config_helpers import build_ai_group_chat_config

TOOL_NAME = "mcp__fake__inspect"


class FakeLLMProtocol(Protocol):
    """描述工具循环测试所需的 LLM 接口。"""

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        provider: str,
        model_name: str,
    ) -> str:
        """返回纯文本响应。"""
        ...

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        provider: str,
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
        provider: str,
        model_name: str,
    ) -> str:
        """记录压缩请求并返回固定文本。"""
        self.text_requests.append(messages[:])
        self.text_models.append((provider, model_name))
        return self.text_response

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        provider: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """记录正式请求并弹出下一条响应。"""
        _ = (tools, tool_choice, parallel_tool_calls)
        self.formal_requests.append(messages[:])
        self.formal_models.append((provider, model_name))
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
                media=(),
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
    *, model_name: str = "main-model", provider: str = "main-vendor",
    tool_result_retention: str = "off", retain_images: bool = False,
    max_tool_rounds: int = 16, forward_reply_threshold_chars: int = 100,
    context_compression_notice: str = "正在整理上下文",
) -> AIGroupChatConfig:
    """构造图片直接交付给主模型的有效配置。"""
    return build_ai_group_chat_config(
        provider=provider, model_name=model_name,
        overrides={
            "tool_result_retention": tool_result_retention,
            "max_tool_rounds": max_tool_rounds,
            "forward_reply_threshold_chars": forward_reply_threshold_chars,
            "context_compression_notice": context_compression_notice,
            "images": {"retain_images": retain_images},
        },
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


def build_tool_call(call_id: str = "call-1") -> LLMToolCall:
    """构造固定信息工具调用。"""
    return LLMToolCall(id=call_id, name=TOOL_NAME, arguments={})





def build_loop(*, config: AIGroupChatConfig, context: FakeContext) -> GroupChatToolLoop:
    """使用实际的图片附件交付服务构造工具循环。"""
    return GroupChatToolLoop(
        config=config, context=cast(Context, context),
        image_delivery=ImageDeliveryService(config=config.images),
    )


async def run_turn(
    *,
    loop: GroupChatToolLoop,
    chat_handler: ContextHandler,
    question: str = "请根据图片回答",
    recent_chat_messages: list[ChatMessage] | None = None,
    turn_messages: list[ChatMessage] | None = None,
    input_image_messages: list[ChatMessage] | None = None,
    input_image_history_messages: list[ChatMessage] | None = None,
    image_turn_state: ImageTurnState | None = None,
) -> None:
    """用空的输入图片结果执行一轮工具循环。"""
    await loop.run(
        msg=build_message(),
        chat_handler=chat_handler,
        recent_chat_messages=recent_chat_messages or [],
        turn_messages=(
            turn_messages
            if turn_messages is not None
            else [ChatMessage(role="user", text=question)]
        ),
        input_image_messages=input_image_messages or [],
        input_image_history_messages=input_image_history_messages or [],
        image_turn_state=(
            image_turn_state if image_turn_state is not None else ImageTurnState()
        ),
    )


class GroupChatToolLoopTest(unittest.IsolatedAsyncioTestCase):
    """验证主模型路由、图片交付、协议状态和上下文保存。"""

    async def test_protocol_leaks_stop_without_sending_or_prompt_injection(self) -> None:
        """异常正文不发送、不保存，也不追加隐藏纠错消息重试。"""
        for leaked in ["<think>不应公开</think>", "💭\n不应公开", "<call:qq__test>{}</call:qq__test>"]:
            with self.subTest(leaked=leaked):
                llm = RecordingLLM(responses=[LLMResponse(content=leaked)])
                context = FakeContext(llm=llm)
                handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=1000000)
                with self.assertRaises(GroupChatTurnError):
                    await run_turn(loop=build_loop(config=build_config(), context=context), chat_handler=handler)
                self.assertEqual(context.bot.sent_texts, [])
                self.assertEqual(context.mcp_tool_manager.calls, [])
                self.assertEqual(len(llm.formal_requests), 1)
                self.assertEqual([message.role for message in handler.messages_lst], ["system", "user"])
                self.assertEqual([message.role for message in llm.formal_requests[0]], ["system", "user"])

    async def test_invalid_mixed_response_is_rejected_before_tool_execution(self) -> None:
        """异常正文与真实工具调用同包返回时整包拒绝。"""
        llm = RecordingLLM(responses=[LLMResponse(
            content="<think>内部内容</think>", tool_calls=[build_tool_call("blocked-call")],
        )])
        context = FakeContext(llm=llm)
        handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=1000000)
        with self.assertRaises(GroupChatTurnError):
            await run_turn(loop=build_loop(config=build_config(tool_result_retention="full"), context=context), chat_handler=handler)
        self.assertEqual(context.mcp_tool_manager.calls, [])
        self.assertEqual(context.bot.sent_texts, [])
        self.assertTrue(all(not message.tool_calls for message in handler.messages_lst))

    async def test_invalid_response_preserves_only_already_sent_content(self) -> None:
        """前轮已发出的正文和工具结果保留，随后异常响应不会产生副作用。"""
        llm = RecordingLLM(responses=[
            LLMResponse(content="已发送的正常正文", tool_calls=[build_tool_call("completed-call")]),
            LLMResponse(content="💭\n内部内容", tool_calls=[build_tool_call("blocked-call")]),
        ])
        context = FakeContext(llm=llm)
        handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=1000000)
        with self.assertRaises(GroupChatTurnError):
            await run_turn(loop=build_loop(config=build_config(tool_result_retention="full"), context=context), chat_handler=handler)
        self.assertEqual(len(llm.formal_requests), 2)
        self.assertEqual(context.bot.sent_texts, ["已发送的正常正文"])
        self.assertEqual(context.mcp_tool_manager.calls, [(TOOL_NAME, {})])
        self.assertEqual([message.tool_call_id for message in handler.messages_lst if message.role == "tool"], ["completed-call"])
        self.assertNotIn("blocked-call", [call.id for message in handler.messages_lst for call in message.tool_calls or []])

    async def test_history_quarantine_preserves_normal_turns_and_tool_pairs(self) -> None:
        """污染只从请求视图隔离，预算和摘要也使用同一份干净历史。"""
        for compress in (False, True):
            with self.subTest(compress=compress):
                llm = RecordingLLM(
                    responses=[LLMResponse(content="继续正常聊天")],
                    text_response="群友约好周六出发，查询结果为三本。",
                )
                context = FakeContext(llm=llm)
                handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=9000)
                normal = "约好周六出发。" * (2000 if compress else 1)
                user = ChatMessage(role="user", text="💭\n用户在讨论这个现象。")
                old_call = build_tool_call("old-completed-call").model_copy(update={
                    "extra_content": {"vendor": {"signature": "old-state"}},
                })
                history = [
                    user,
                    ChatMessage(role="assistant", text=normal),
                    ChatMessage(role="assistant", text="💭\nprivate-plan" + "x" * 30000),
                    ChatMessage(
                        role="assistant", text="🫧\nprivate-tool-plan",
                         tool_calls=[old_call],
                    ),
                    ChatMessage(role="tool", tool_call_id=old_call.id, text="历史查询结果：三本"),
                ]
                original = [message.model_copy(deep=True) for message in history]
                handler.add_msg(msg_list=history)
                await run_turn(
                    loop=build_loop(config=build_config(), context=context),
                    chat_handler=handler,
                )

                self.assertEqual(history, original)
                self.assertEqual(len(llm.text_requests), int(compress))
                for request in [*llm.formal_requests, *llm.text_requests]:
                    text = "\n".join((m.text or "") for m in request)
                    self.assertNotIn("private-", text)
                request = llm.text_requests[0] if compress else llm.formal_requests[0]
                text = "\n".join(m.text or "" for m in request)
                self.assertIn(normal, text)
                self.assertIn(user.text or "", text)
                self.assertIn("历史查询结果：三本", text)
                if not compress:
                    replay = next(m for m in request if m.tool_calls)
                    self.assertIsNone(replay.text)
                    self.assertEqual(replay.tool_calls, [old_call])
                    self.assertIn(old_call.id, [m.tool_call_id for m in request if m.role == "tool"])
                    self.assertEqual(handler.messages_lst[1:1 + len(history)], original)
                self.assertEqual(context.mcp_tool_manager.calls, [])
                self.assertEqual(handler.messages_lst[-1].text, "继续正常聊天")

    async def test_large_tool_results_finish_then_compress_only_retained_history(self) -> None:
        """完整交付多轮大结果；full 保存后触发压缩，off 和 summary 不积累原文。"""
        large_result = "工具资料" * 10000
        for retention in ("off", "summary", "full"):
            with self.subTest(retention=retention):
                llm = RecordingLLM(
                    responses=[
                        LLMResponse(

                            tool_calls=[build_tool_call("call-1")],
                        ),
                        LLMResponse(tool_calls=[build_tool_call("call-2")]),
                        LLMResponse(content="工具查询成功"),
                        LLMResponse(content="下一轮成功"),
                        LLMResponse(content="再次成功"),
                    ],
                    text_response="工具资料已整理成摘要",
                )
                context = FakeContext(llm=llm)
                loop = build_loop(
                    config=build_config(
                         tool_result_retention=retention,
                    ),
                    context=context,
                )
                handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=9000)
                with patch.object(
                    context.mcp_tool_manager,
                    "call_tool_with_artifacts",
                    return_value=LLMToolExecutionResult(result={"ok": True, "text": large_result}),
                ):
                    await run_turn(loop=loop, chat_handler=handler)

                request = llm.formal_requests[2]
                self.assertGreater(loop.token_estimator.estimate_messages(messages=request), 9000)
                results = [message for message in request if message.role == "tool"]
                self.assertEqual([message.tool_call_id for message in results], ["call-1", "call-2"])
                self.assertEqual(
                    [call.id for message in request for call in message.tool_calls or []],
                    ["call-1", "call-2"],
                )
                for result in results:
                    self.assertIn(large_result, result.text or "")
                    self.assertEqual(result in handler.messages_lst, retention == "full")
                self.assertEqual(llm.text_requests, [])
                self.assertEqual(context.bot.sent_texts, ["工具查询成功"])

                await run_turn(loop=loop, chat_handler=handler, question="继续")
                await run_turn(loop=loop, chat_handler=handler, question="再继续")
                self.assertEqual(len(llm.text_requests), 1 if retention == "full" else 0)
                self.assertEqual(len(llm.formal_requests), 5)
                self.assertEqual(handler.messages_lst[-1].text, "再次成功")
                if retention == "full":
                    self.assertIn(large_result, llm.text_requests[0][1].text or "")
                    self.assertIn("工具资料已整理成摘要", handler.messages_lst[1].text or "")
                elif retention == "summary":
                    statuses = [message.text or "" for message in handler.messages_lst if "运行记录" in (message.text or "")]
                    self.assertEqual(len(statuses), 2)
                    self.assertTrue(all("成功 1 次" in status and "失败" not in status for status in statuses))

    async def test_large_reply_history_compresses_above_budget_and_recovers(self) -> None:
        """压缩请求可超出长期预算，预算小于压缩提示词时后续对话仍能继续。"""
        for max_tokens in (700, 9000):
            with self.subTest(max_tokens=max_tokens):
                llm = RecordingLLM(
                    responses=[
                        LLMResponse(content="历史回复" * 2000),
                        LLMResponse(content="继续成功"),
                        LLMResponse(content="再次成功"),
                    ],
                    text_response="已整理的历史摘要",
                )
                context = FakeContext(llm=llm)
                loop = build_loop(config=build_config(), context=context)
                handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=max_tokens)
                await run_turn(loop=loop, chat_handler=handler, question="开始")
                await run_turn(loop=loop, chat_handler=handler, question="继续")
                await run_turn(loop=loop, chat_handler=handler, question="继续")
                self.assertEqual(len(llm.text_requests), 1)
                self.assertGreater(
                    loop.token_estimator.estimate_messages(messages=llm.text_requests[0]),
                    max_tokens,
                )
                self.assertEqual(len(llm.formal_requests), 3)
                self.assertEqual(handler.messages_lst[-1].text, "再次成功")

    async def test_large_current_input_does_not_trigger_empty_history_compression(self) -> None:
        """首轮大输入直接交付，保存后在下一轮压缩；临时视觉消息不进入长期历史。"""
        llm = RecordingLLM(responses=[LLMResponse(content="收到"), LLMResponse(content="继续成功")])
        context = FakeContext(llm=llm)
        loop = build_loop(config=build_config(), context=context)
        handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=9000)
        question = "当前大段输入" * 3000
        image_message = ChatMessage(role="user", text="临时图片说明" * 3000, image=[b"image"])
        await run_turn(
            loop=loop, chat_handler=handler, question=question,
            input_image_messages=[image_message],
        )
        self.assertEqual(llm.text_requests, [])
        self.assertEqual(llm.formal_requests[0][1].text, question)
        self.assertIs(llm.formal_requests[0][2], image_message)
        self.assertNotIn(image_message, handler.messages_lst)
        await run_turn(loop=loop, chat_handler=handler, question="继续")
        self.assertEqual(len(llm.text_requests), 1)
        self.assertIn(question, llm.text_requests[0][1].text or "")
        self.assertNotIn("临时图片说明", llm.text_requests[0][1].text or "")
        self.assertEqual(handler.messages_lst[-1].text, "继续成功")

    async def test_compression_can_reduce_summary_without_reserving_current_input(self) -> None:
        """超限摘要继续缩短，当前长输入与图片保持独立且不挤占摘要预算。"""
        llm = RecordingLLM(responses=[LLMResponse(content="压缩后回复")])
        context = FakeContext(llm=llm)
        loop = build_loop(config=build_config( retain_images=True), context=context)
        handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=700)
        handler.add_msg(ChatMessage(role="assistant", text="历史" * 3000))
        current_message = ChatMessage(role="user", text="本轮新问题" * 1000, image=[b"current"], image_detail="high")
        image_message = ChatMessage(role="user", text="临时图片", image=[b"image"], image_detail="low")
        with patch.object(
            llm, "get_ai_text_response", side_effect=["长摘要" * 200, "简短摘要"],
        ) as compress:
            await run_turn(
                loop=loop, chat_handler=handler,
                turn_messages=[current_message], input_image_messages=[image_message],
            )
        self.assertEqual(compress.await_count, 2)
        request = llm.formal_requests[0]
        self.assertEqual([message.role for message in request], ["system", "user", "user", "user"])
        self.assertIn("简短摘要", request[1].text or "")
        self.assertIs(request[2], current_message)
        self.assertIs(request[3], image_message)
        self.assertLessEqual(loop.token_estimator.estimate_messages(messages=request[:2]), 700)
        self.assertNotIn(image_message, handler.messages_lst)
        self.assertEqual(handler.messages_lst[-1].text, "压缩后回复")

    async def test_compression_notice_failure_still_allows_reply(self) -> None:
        """提示发送失败只记录日志，压缩和正式回复继续。"""
        llm = RecordingLLM(responses=[LLMResponse(content="压缩后回复")])
        context = FakeContext(llm=llm)
        handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=9000)
        handler.add_msg(ChatMessage(role="assistant", text="历史" * 3000))
        with patch.object(
            context.bot, "send_msg",
            side_effect=[NapCatSendMessageError("通知发送失败"), Response(status="ok", retcode=0)],
        ) as send:
            await run_turn(loop=build_loop(config=build_config(), context=context), chat_handler=handler)
        self.assertEqual(send.await_count, 2)
        self.assertEqual(len(llm.text_requests), 1)
        self.assertEqual(len(llm.formal_requests), 1)
        self.assertEqual(handler.messages_lst[-1].text, "压缩后回复")

    async def test_invalid_summary_preserves_history_and_allows_retry(self) -> None:
        """空白或无法缩短的摘要明确失败，保留原历史供后续重试。"""
        history = ChatMessage(role="assistant", text="历史回复" * 2000)
        for summary, error in ((" \n\t ", "空历史摘要"), (history.text, "未能缩短")):
            assert summary is not None
            with self.subTest(error=error):
                llm = RecordingLLM(
                    responses=[LLMResponse(content="重试成功")],
                    text_response=summary,
                )
                context = FakeContext(llm=llm)
                loop = build_loop(config=build_config(), context=context)
                handler = ContextHandler(system_prompt="角色提示词", max_context_tokens=9000)
                handler.add_msg(history)
                original_messages = handler.messages_lst
                with self.assertRaisesRegex(LLMRequestError, error):
                    await run_turn(loop=loop, chat_handler=handler)
                self.assertEqual(llm.formal_requests, [])
                self.assertEqual(handler.messages_lst, original_messages)

                llm.text_response = "可用摘要"
                await run_turn(loop=loop, chat_handler=handler)
                self.assertEqual(handler.messages_lst[-1].text, "重试成功")







    async def test_response_items_only_continue_current_tool_round(self) -> None:
        """原始 Responses 状态用于工具续问，不展示也不跨用户轮次保存。"""
        items: list[JsonObject] = [
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-state"},
            {"type": "function_call", "id": "fc_1", "call_id": "call-1", "name": TOOL_NAME, "arguments": "{}"},
        ]
        llm = RecordingLLM(responses=[
            LLMResponse(content="我先查一下", response_items=items, tool_calls=[build_tool_call()]),
            LLMResponse(content="工具后回复"),
        ])
        context = FakeContext(llm=llm)
        handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)
        await run_turn(loop=build_loop(config=build_config(tool_result_retention="full"), context=context), chat_handler=handler)
        assistant_call = next(message for message in llm.formal_requests[1] if message.tool_calls)
        self.assertEqual(assistant_call.response_items, items)
        self.assertEqual(context.bot.sent_texts, ["我先查一下", "工具后回复"])
        self.assertTrue(all(not message.response_items for message in handler.messages_lst))
        self.assertTrue(any(message.tool_calls for message in handler.messages_lst))

    async def test_recent_chat_is_temporary_and_system_stays_at_start(self) -> None:
        """工具续问保留首部系统要求，最近群聊不进入长期上下文。"""
        llm = RecordingLLM(responses=[
            LLMResponse(tool_calls=[build_tool_call()]), LLMResponse(content="查到了，挺离谱的"),
        ])
        context = FakeContext(llm=llm)
        handler = ContextHandler(system_prompt="人格\n知识库\n其他要求", max_context_tokens=1000000)
        recent = ChatMessage(role="user", text="[12:01] 小明(20001)：食堂又涨价了")
        await run_turn(loop=build_loop(config=build_config(), context=context), chat_handler=handler,
                       question="你怎么看", recent_chat_messages=[recent])
        self.assertEqual(len(llm.formal_requests), 2)
        for request in llm.formal_requests:
            self.assertEqual(request[0], handler.system_prompt)
            self.assertEqual([message.role for message in request].count("system"), 1)
            self.assertIn(recent, request)
        self.assertEqual(llm.formal_requests[1][-1].role, "tool")
        self.assertNotIn(recent, handler.messages_lst)
        self.assertEqual(context.bot.sent_texts, ["查到了，挺离谱的"])

    async def test_retained_tool_history_is_bounded_summary(self) -> None:
        """开启工具保留时只保存调用状态，不保存原始结果或重复正文。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(
                    content="我先查一下",

                    tool_calls=[build_tool_call()],
                ),
                LLMResponse(content="工具后回复"),
            ]
        )
        context = FakeContext(llm=llm)
        config = build_config(tool_result_retention="summary")
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
        )

        history_text = "\n".join(
            message.text or "" for message in chat_handler.messages_lst
        )
        history_roles = [message.role for message in chat_handler.messages_lst]
        self.assertNotIn("tool", history_roles)
        self.assertNotIn("工具结果", history_text)
        self.assertIn(TOOL_NAME, history_text)
        self.assertIn("成功 1 次", history_text)
        self.assertEqual(
            [message.text for message in chat_handler.messages_lst].count(
                "我先查一下"
            ),
            1,
        )

    async def test_full_tool_retention_preserves_arguments_and_result(self) -> None:
        """full 模式把可继续使用的工具调用和完整结果写入长期上下文。"""
        tool_call = LLMToolCall(
            id="call-full",
            name=TOOL_NAME,
            arguments={"query": "完整参数"},
        )
        llm = RecordingLLM(
            responses=[
                LLMResponse(content="我来检查", tool_calls=[tool_call]),
                LLMResponse(content="检查完成"),
            ]
        )
        context = FakeContext(llm=llm)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        await run_turn(
            loop=build_loop(
                config=build_config(tool_result_retention="full"),
                context=context,
            ),
            chat_handler=chat_handler,
        )

        retained_tool_call = next(
            message
            for message in chat_handler.messages_lst
            if message.tool_calls
        )
        self.assertEqual(retained_tool_call.tool_calls, [tool_call])
        retained_result = next(
            message
            for message in chat_handler.messages_lst
            if message.role == "tool"
        )
        self.assertIn("工具结果", retained_result.text or "")
        self.assertEqual(retained_result.tool_call_id, "call-full")

    async def test_sent_content_survives_tool_round_exhaustion(self) -> None:
        """正文发送后即使工具轮数耗尽，后续请求仍能看到该回复。"""
        llm = RecordingLLM(
            responses=[
                LLMResponse(
                    content="已经发到群里的正文",
                    tool_calls=[build_tool_call()],
                )
            ]
        )
        context = FakeContext(llm=llm)
        config = build_config(max_tool_rounds=1)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )

        with self.assertRaisesRegex(RuntimeError, "超过最大轮数"):
            await run_turn(
                loop=build_loop(config=config, context=context),
                chat_handler=chat_handler,
            )

        self.assertEqual(context.bot.sent_texts, ["已经发到群里的正文"])
        self.assertEqual(
            [message.role for message in chat_handler.messages_lst],
            ["system", "user", "assistant"],
        )
        self.assertEqual(
            chat_handler.messages_lst[-1].text,
            "已经发到群里的正文",
        )

    async def test_model_name_does_not_add_temporary_prompt(self) -> None:
        """模型名称不再触发 DSV4 临时 user 消息。"""
        llm = RecordingLLM(responses=[LLMResponse(content="正常回复")])
        context = FakeContext(llm=llm)
        config = build_config(
            model_name="example-model",
            provider="main",
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
            [("main", "example-model")],
        )





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
        config = build_config()
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
        self.assertTrue(all(not message.image for message in llm.formal_requests[1]))
        self.assertIn("下载超时", second_text)
        self.assertIn("2 张图片", second_text)

    async def test_repeated_tool_image_is_observed_once_per_turn(self) -> None:
        """同一轮重复返回相同图片时只在主模型上下文附带一次。"""
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

        self.assertEqual(llm.text_requests, [])
        final_observations = [
            message
            for message in llm.formal_requests[-1]
            if message.image == [b"same-image"]
        ]
        self.assertEqual(len(final_observations), 1)

    async def test_forward_tool_auto_fetches_images_for_main_model(self) -> None:
        """只读取合并转发时会自动补取其中图片并直接交给主模型。"""
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
        self.assertEqual(llm.text_models, [])
        self.assertEqual([image for message in llm.formal_requests[1] for image in message.image or []], [b"tool-image"])
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
        """非法 content 标记不会发送，并返回错误事实供主模型恢复。"""
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
        self.assertIn("未发送", retry_text)
        self.assertNotIn("请重新生成", retry_text)
        self.assertIn("@全体", retry_text)
        self.assertEqual(
            chat_handler.messages_lst[-1].text,
            "<Reply>\n我在喵~ 已改成合法标记。",
        )

    async def test_video_is_kept_in_current_request_only(self) -> None:
        """图片保留开关不会把视频带入下一轮；文字来源继续保留。"""
        for retain_images in (False, True):
            with self.subTest(retain_images=retain_images):
                llm = RecordingLLM(responses=[LLMResponse(content="视频答复"), LLMResponse(content="后续答复")])
                context = FakeContext(llm=llm)
                config = build_config( retain_images=retain_images)
                handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)
                loop = build_loop(config=config, context=context)
                await run_turn(loop=loop, chat_handler=handler, turn_messages=[
                    ChatMessage(role="user", text="当前消息第 1 个视频", video=[b"original-video"]),
                ])
                self.assertTrue(any(item.video for item in llm.formal_requests[0]))
                self.assertFalse(any(item.video for item in handler.messages_lst))
                await run_turn(loop=loop, chat_handler=handler, question="继续")
                self.assertFalse(any(item.video for item in llm.formal_requests[1]))
                self.assertIn("当前消息第 1 个视频", [item.text for item in handler.messages_lst])

    async def test_plain_content_sends_once_and_finishes(self) -> None:
        """正常正文、HTML 和单个协议标签的引用示例均可发送，无需纠错。"""
        for content in (
            "第一句。I think so。HTML 示例：<div>普通内容</div>；标签名示例：`<thought>`。",
            "这个标记的字面写法是 `🫧thought`，不是正常聊天里的单词。",
            "I thought we should meet at six. Let's check the timetable.",
            "💭 我再想想，明天八点见。",
            "💭",
            "这个符号 `💭` 表示思考。\n计算过程可以正常解释：9 点减去 60 分钟就是 8 点。",
        ):
            with self.subTest(content=content):
                llm = RecordingLLM(responses=[LLMResponse(content=content)])
                context = FakeContext(llm=llm)
                chat_handler = ContextHandler(system_prompt="系统提示词", max_context_tokens=1000000)
                await run_turn(
                    loop=build_loop(config=build_config(), context=context),
                    chat_handler=chat_handler, question="继续说",
                )
                self.assertEqual(context.bot.sent_texts, [content])
                self.assertEqual(len(llm.formal_requests), 1)
                self.assertEqual(chat_handler.messages_lst[-1].text, content)

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
        chat_handler.add_msg(
            msg_list=[
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

    async def test_direct_mode_can_retain_original_images_across_turns(self) -> None:
        """用户显式开启 retain_images 后，后续请求继续携带原始图片字节。"""
        llm = RecordingLLM(responses=[LLMResponse(content="下一轮回复")])
        context = FakeContext(llm=llm)
        config = build_config( retain_images=True)
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=1000000
        )
        chat_handler.add_msg(
            msg=ChatMessage(
                role="user",
                text="上一轮图片",
                image=[b"old-original-image"],
                image_detail="high",
            )
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="继续看上一张图",
        )

        self.assertEqual(
            llm.formal_requests[0][1].image,
            [b"old-original-image"],
        )
        self.assertEqual(
            chat_handler.messages_lst[1].image,
            [b"old-original-image"],
        )

    async def test_context_compression_and_formal_reply_use_main_model(self) -> None:
        """上下文压缩与压缩后的正式请求都固定使用主模型。"""
        llm = RecordingLLM(
            responses=[LLMResponse(content="压缩后回复")],
            text_response="历史上下文摘要",
        )
        context = FakeContext(llm=llm)
        config = build_config(
            model_name="example-model",
            provider="main-vendor",
            context_compression_notice="我先整理一下记忆",
        )
        chat_handler = ContextHandler(
            system_prompt="系统提示词", max_context_tokens=9000
        )
        chat_handler.add_msg(
            msg_list=[
                ChatMessage(role="user", text="历史消息一" * 1000),
                ChatMessage(role="assistant", text="历史回复一" * 1000),
            ]
        )

        await run_turn(
            loop=build_loop(config=config, context=context),
            chat_handler=chat_handler,
            question="当前新消息",
        )

        self.assertEqual(llm.text_models, [("main-vendor", "example-model")])
        self.assertEqual(llm.formal_models, [("main-vendor", "example-model")])
        compression_text = llm.text_requests[0][1].text or ""
        self.assertIn("历史消息一", compression_text)
        self.assertNotIn("当前新消息", compression_text)
        self.assertEqual(
            [message.role for message in llm.formal_requests[0]],
            ["system", "user", "user"],
        )
        rebuilt_text = llm.formal_requests[0][1].text or ""
        self.assertIn("历史上下文摘要", rebuilt_text)
        self.assertNotIn("当前新消息", rebuilt_text)
        self.assertEqual(llm.formal_requests[0][2].text, "当前新消息")
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
        config = build_config(forward_reply_threshold_chars=5)
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
