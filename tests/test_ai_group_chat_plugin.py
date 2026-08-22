"""AI 群聊插件端到端假 Bot / 假 LLM 烟测。"""

import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from typing import cast

import httpx

from app.config import (
    AIGroupChatConfig,
    AIGroupConfig,
    ConfigManager,
    MaterializedAIGroupChatConfig,
    MaterializedAIGroupConfig,
    PluginConfigSnapshot,
    PluginConfigView,
)
from app.database import GroupDataScope, StoredGroupMessage
from app.models import (
    At,
    GroupMessage,
    Image,
    JsonObject,
    MessageSegment,
    Response,
    Sender,
    Text,
)
from app.plugins.ai_group_chat.ai_group_chat import AIGroupChatPlugin
from app.plugins.ai_group_chat.tool_loop import TurnContextState
from app.plugins.base import Context
from app.services import (
    ChatMessage,
    ConversationContextKey,
    ConversationContextStore,
)
from app.services.llm.schemas import LLMResponse, LLMToolChoice, LLMToolDefinition


class SmokeBot:
    """提供图片刷新和群消息发送能力的假 Bot。"""

    def __init__(self, *, bot_id: str = "10000") -> None:
        self.boot_id = bot_id
        self.image_calls: list[tuple[str | None, str | None]] = []
        self.sent_texts: list[str] = []

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        self.image_calls.append((file_id, file))
        return Response(
            status="ok",
            retcode=0,
            data={"base64": "c21va2UtaW1hZ2U="},
        )

    async def send_msg(
        self,
        *,
        group_id: str,
        text: str | None = None,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        _ = group_id
        if text is not None:
            self.sent_texts.append(text)
        if message_segment is not None:
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
        _ = (group_id, messages)
        raise AssertionError("短回复不应使用合并转发")


class SmokeDatabase:
    """引用消息查询始终为空。"""

    async def get_active(
        self, *, scope: GroupDataScope, message_id: str
    ) -> StoredGroupMessage | None:
        _ = (scope, message_id)
        return None


class EmptyToolManager:
    """不暴露 MCP 工具。"""

    def list_tools(self) -> list[LLMToolDefinition]:
        return []

    async def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        _ = (name, arguments)
        raise KeyError(name)


class SmokeLLM:
    """视觉请求返回描述，正式请求返回最终 content。"""

    def __init__(self) -> None:
        self.vision_models: list[tuple[str, str]] = []
        self.formal_models: list[tuple[str, str]] = []
        self.formal_messages: list[list[ChatMessage]] = []
        self.formal_entered = asyncio.Event()
        self.formal_release: asyncio.Event | None = None
        self.active_formal_requests: int = 0
        self.max_active_formal_requests: int = 0

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        provider: str,
        model_name: str,
        max_attempts: int | None = None,
        retry_delay_seconds: float | None = None,
    ) -> str:
        _ = (max_attempts, retry_delay_seconds)
        self.vision_models.append((provider, model_name))
        if [message.role for message in messages] != ["system", "user"]:
            raise AssertionError("视觉请求不应携带群聊历史")
        return "图片中写着“测试成功”。"

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        provider: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        _ = (tools, tool_choice, parallel_tool_calls)
        self.formal_models.append((provider, model_name))
        self.formal_messages.append(messages[:])
        self.active_formal_requests += 1
        self.max_active_formal_requests = max(
            self.max_active_formal_requests,
            self.active_formal_requests,
        )
        self.formal_entered.set()
        try:
            if self.formal_release is not None:
                await self.formal_release.wait()
            return LLMResponse(
                content="图片里写着测试成功。",
                reasoning_content="这段内容不能发到群里",
            )
        finally:
            self.active_formal_requests -= 1


class ControlledSmokeLLM(SmokeLLM):
    """允许测试按消息 ID 独立控制并发请求的完成顺序。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered_by_message_id: dict[str, asyncio.Event] = {}
        self.release_by_message_id: dict[str, asyncio.Event] = {}
        self.messages_by_message_id: dict[str, list[ChatMessage]] = {}

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        provider: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        _ = (tools, tool_choice, parallel_tool_calls)
        message_id = self._message_id(messages=messages)
        self.formal_models.append((provider, model_name))
        self.formal_messages.append(messages[:])
        self.messages_by_message_id[message_id] = messages[:]
        self.active_formal_requests += 1
        self.max_active_formal_requests = max(
            self.max_active_formal_requests,
            self.active_formal_requests,
        )
        self.entered_by_message_id.setdefault(message_id, asyncio.Event()).set()
        try:
            release = self.release_by_message_id.get(message_id)
            if release is not None:
                await release.wait()
            return LLMResponse(content=f"回复 {message_id}")
        finally:
            self.active_formal_requests -= 1

    def _message_id(self, *, messages: list[ChatMessage]) -> str:
        """从当前用户消息的 Markdown 元数据中读取消息 ID。"""
        for message in reversed(messages):
            if message.role != "user" or message.text is None:
                continue
            match = re.search(r"^- 消息 ID: (\d+)$", message.text, re.MULTILINE)
            if match is not None:
                return match.group(1)
        raise AssertionError("正式请求缺少当前消息 ID")


class SmokeContext:
    """组合烟测依赖。"""

    def __init__(
        self,
        *,
        bot_id: str = "10000",
        conversation_contexts: ConversationContextStore | None = None,
    ) -> None:
        self.bot = SmokeBot(bot_id=bot_id)
        self.group_messages = SmokeDatabase()
        self.conversation_contexts = (
            conversation_contexts or ConversationContextStore()
        )
        self.direct_httpx = cast(httpx.AsyncClient, object())
        self.llm = SmokeLLM()
        self.mcp_tool_manager = EmptyToolManager()


class FakeConfigManager:
    """只提供插件消费的配置快照。"""

    def __init__(self, snapshot: PluginConfigSnapshot) -> None:
        self.plugins = snapshot


def ai_plugin_config(manager: FakeConfigManager) -> PluginConfigView:
    """构造只暴露 AI 群聊配置的测试视图。"""
    return PluginConfigView(
        manager=cast(ConfigManager, manager),
        plugin_id="ai_group_chat",
    )


def build_snapshot(
    *,
    revision: int = 1,
    system_prompt: str = "角色、知识库和通用群聊要求",
    max_context_tokens: int = 1_000_000,
    model_name: str = "main-model",
    include_second_group: bool = False,
    debug_dump_messages: bool = False,
) -> PluginConfigSnapshot:
    """构造已经读取提示词文件的 AI 配置快照。"""
    group = AIGroupConfig(
        id="40000",
        system_prompt_file="roles/default.md",
        max_context_tokens=max_context_tokens,
    )
    group_configs = [group]
    if include_second_group:
        group_configs.append(
            AIGroupConfig(
                id="40001",
                system_prompt_file="roles/default.md",
                max_context_tokens=max_context_tokens,
            )
        )
    source = AIGroupChatConfig.model_validate(
        {
            "model": {
            "provider": "main-vendor",
            "name": model_name,
            "supports_images": False,
        },
            "vision": {
            "model": {"provider": "vision-vendor", "name": "vision-model"},
            "system_prompt_file": "vision/system.md",
            "user_prompt_file": "vision/user.md",
            "retain_descriptions": True,
        },
            "show_reasoning": False,
            "debug_dump_messages": debug_dump_messages,
            "groups": group_configs,
        }
    )
    materialized = MaterializedAIGroupChatConfig(
        source=source,
        groups=tuple(
            MaterializedAIGroupConfig(
                source=group_config,
                system_prompt=system_prompt,
            )
            for group_config in group_configs
        ),
        vision_system_prompt="只描述可见事实。",
        vision_user_prompt="结合当前问题描述图片。",
    )
    return PluginConfigSnapshot(
        revision=revision,
        ai_group_chat=materialized,
        group_notice=None,
        auto_unban=None,
        image_generate=None,
        neavo_image_generate=None,
        recall_bot_image=None,
        referenced_files=frozenset(),
    )


def build_event(
    message_id: str = "30000",
    *,
    bot_id: str = "10000",
    group_id: str = "40000",
) -> GroupMessage:
    """构造艾特机器人并附图的群消息。"""
    return GroupMessage(
        time=1_777_132_900,
        self_id=bot_id,
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id=message_id,
        group_id=group_id,
        group_name="测试群",
        message=[At.new(bot_id), Text.new("请看图回答"), Image.new("smoke.png")],
        raw_message=f"[CQ:at,qq={bot_id}]请看图回答[图片]",
        sender=Sender(user_id="20000", nickname="测试用户", role="member"),
    )


def conversation_key(
    *, bot_id: str = "10000", group_id: str = "40000"
) -> ConversationContextKey:
    """返回 AI 群聊测试使用的进程内对话键。"""
    return ConversationContextKey(
        owner=AIGroupChatPlugin.plugin_id,
        bot_id=bot_id,
        conversation_id=group_id,
    )


class AIGroupChatPluginSmokeTest(unittest.IsolatedAsyncioTestCase):
    """验证附图消息从读取到最终群回复的完整编排。"""

    async def test_non_image_main_model_uses_internal_vision_tool(self) -> None:
        """独立视觉模型描述图片，正式回复始终由主模型生成。"""
        smoke_context = SmokeContext()
        plugin = AIGroupChatPlugin(
            context=cast(Context, smoke_context),
            plugin_config=ai_plugin_config(FakeConfigManager(build_snapshot())),
        )
        try:
            handled = await plugin.run(build_event())
        finally:
            await plugin.stop_consumers()

        self.assertTrue(handled)
        self.assertEqual(smoke_context.bot.image_calls, [(None, "smoke.png")])
        self.assertEqual(
            smoke_context.llm.vision_models,
            [("vision-vendor", "vision-model")],
        )
        self.assertEqual(
            smoke_context.llm.formal_models,
            [("main-vendor", "main-model")],
        )
        request_text = "\n".join(
            message.text or "" for message in smoke_context.llm.formal_messages[0]
        )
        self.assertIn("系统生成，不是用户原话", request_text)
        self.assertIn("图片中写着“测试成功”", request_text)
        self.assertEqual(smoke_context.bot.sent_texts, ["图片里写着测试成功。"])

    async def test_same_group_requests_use_isolated_context_and_commit_by_completion(
        self,
    ) -> None:
        """同群请求并发执行，且只按回复完成顺序提交各自完整轮次。"""
        smoke_context = SmokeContext()
        controlled_llm = ControlledSmokeLLM()
        controlled_llm.release_by_message_id = {
            "30001": asyncio.Event(),
            "30002": asyncio.Event(),
        }
        smoke_context.llm = controlled_llm
        plugin = AIGroupChatPlugin(
            context=cast(Context, smoke_context),
            plugin_config=ai_plugin_config(
                FakeConfigManager(build_snapshot(debug_dump_messages=True))
            ),
        )
        dump_directory = tempfile.TemporaryDirectory()
        self.addCleanup(dump_directory.cleanup)
        runtime = plugin._current_runtime()  # pyright: ignore[reportPrivateUsage]
        assert runtime is not None
        runtime.debug_dumper.root_dir = Path(dump_directory.name)
        first = asyncio.create_task(plugin.run(build_event("30001")))
        second: asyncio.Task[bool] | None = None
        try:
            async with asyncio.timeout(1):
                while "30001" not in controlled_llm.entered_by_message_id:
                    await asyncio.sleep(0.01)
                await controlled_llm.entered_by_message_id["30001"].wait()
            second = asyncio.create_task(plugin.run(build_event("30002")))
            async with asyncio.timeout(1):
                while "30002" not in controlled_llm.entered_by_message_id:
                    await asyncio.sleep(0.01)
                await controlled_llm.entered_by_message_id["30002"].wait()
            self.assertEqual(controlled_llm.active_formal_requests, 2)
            self.assertEqual(controlled_llm.max_active_formal_requests, 2)
            self.assertNotIn(
                "30001",
                "\n".join(
                    message.text or ""
                    for message in controlled_llm.messages_by_message_id["30002"]
                ),
            )

            controlled_llm.release_by_message_id["30002"].set()
            self.assertTrue(await second)
            self.assertFalse(first.done())
            context_after_second = smoke_context.conversation_contexts.get(
                key=conversation_key()
            )
            assert context_after_second is not None
            context_after_second_text = "\n".join(
                message.text or "" for message in context_after_second.messages_lst
            )
            self.assertIn("30002", context_after_second_text)
            self.assertNotIn("30001", context_after_second_text)
            self.assertEqual(
                [
                    message.text
                    for message in context_after_second.messages_lst
                    if message.role == "assistant"
                ],
                ["回复 30002"],
            )

            controlled_llm.release_by_message_id["30001"].set()
            self.assertTrue(await first)
        finally:
            for release in controlled_llm.release_by_message_id.values():
                release.set()
            if not first.done():
                await first
            if second is not None and not second.done():
                await second
            await plugin.stop_consumers()

        committed_context = smoke_context.conversation_contexts.get(
            key=conversation_key()
        )
        assert committed_context is not None
        committed_texts = [message.text or "" for message in committed_context.messages_lst]
        fast_user_index = next(
            index for index, text in enumerate(committed_texts) if "30002" in text
        )
        fast_reply_index = committed_texts.index("回复 30002")
        slow_user_index = next(
            index for index, text in enumerate(committed_texts) if "30001" in text
        )
        slow_reply_index = committed_texts.index("回复 30001")
        self.assertEqual(
            [
                message.text
                for message in committed_context.messages_lst
                if message.role == "assistant"
            ],
            ["回复 30002", "回复 30001"],
        )
        self.assertLess(fast_user_index, fast_reply_index)
        self.assertLess(fast_reply_index, slow_user_index)
        self.assertLess(slow_user_index, slow_reply_index)
        dump_files = list(Path(dump_directory.name).glob("40000/*.md"))
        self.assertEqual(len(dump_files), 1)
        dump_content = dump_files[0].read_text(encoding="utf-8")
        self.assertLess(dump_content.index("回复 30002"), dump_content.index("回复 30001"))

    async def test_different_groups_can_run_in_parallel(self) -> None:
        """不同群使用不同锁，正式请求可以同时进行。"""
        smoke_context = SmokeContext()
        smoke_context.llm.formal_release = asyncio.Event()
        plugin = AIGroupChatPlugin(
            context=cast(Context, smoke_context),
            plugin_config=ai_plugin_config(
                FakeConfigManager(build_snapshot(include_second_group=True))
            ),
        )
        first = asyncio.create_task(plugin.run(build_event("30005")))
        second = asyncio.create_task(
            plugin.run(build_event("30006", group_id="40001"))
        )
        try:
            async with asyncio.timeout(1):
                while len(smoke_context.llm.formal_models) < 2:
                    await asyncio.sleep(0.01)
            self.assertEqual(smoke_context.llm.active_formal_requests, 2)
            smoke_context.llm.formal_release.set()
            self.assertEqual(await asyncio.gather(first, second), [True, True])
        finally:
            smoke_context.llm.formal_release.set()
            for task in (first, second):
                if not task.done():
                    await task
            await plugin.stop_consumers()

        self.assertEqual(smoke_context.llm.max_active_formal_requests, 2)

    async def test_stale_compression_cannot_overwrite_a_faster_turn(self) -> None:
        """慢请求基于旧历史生成的摘要只能追加本轮，不能覆盖先完成的请求。"""
        smoke_context = SmokeContext()
        plugin = AIGroupChatPlugin(
            context=cast(Context, smoke_context),
            plugin_config=ai_plugin_config(FakeConfigManager(build_snapshot())),
        )
        key = conversation_key()
        runtime = plugin._current_runtime()  # pyright: ignore[reportPrivateUsage]
        assert runtime is not None
        group = runtime.groups["40000"]
        lock = smoke_context.conversation_contexts.lock_for(key=key)
        async with lock:
            persistent = plugin._get_group_context(  # pyright: ignore[reportPrivateUsage]
                runtime=runtime,
                group=group,
                key=key,
            )
            persistent.build_chatmessage(
                message=ChatMessage(role="assistant", text="压缩前历史")
            )
            base_revision = persistent.revision
            fast_temporary = persistent.fork()
            slow_temporary = persistent.fork()

        fast_temporary.replace_history(
            messages=[
                ChatMessage(role="user", text="快速请求生成的摘要和正文"),
                ChatMessage(role="assistant", text="快速回复"),
            ]
        )
        slow_temporary.replace_history(
            messages=[
                ChatMessage(role="user", text="过期摘要，不应提交"),
                ChatMessage(role="assistant", text="慢速回复"),
            ]
        )
        fast_state = TurnContextState(
            fallback_turn_messages=[ChatMessage(role="user", text="快速用户消息")],
            persisted_prefix_count=2,
            replace_existing_history=True,
            commit_requested=True,
            title="快速请求",
        )
        slow_state = TurnContextState(
            fallback_turn_messages=[ChatMessage(role="user", text="慢速用户消息")],
            persisted_prefix_count=2,
            replace_existing_history=True,
            commit_requested=True,
            title="慢速请求",
        )
        try:
            self.assertIsNotNone(
                await plugin._commit_turn_context(  # pyright: ignore[reportPrivateUsage]
                    msg=build_event("30011"),
                    context_key=key,
                    persistent_context=persistent,
                    base_revision=base_revision,
                    temporary_context=fast_temporary,
                    state=fast_state,
                )
            )
            self.assertIsNotNone(
                await plugin._commit_turn_context(  # pyright: ignore[reportPrivateUsage]
                    msg=build_event("30012"),
                    context_key=key,
                    persistent_context=persistent,
                    base_revision=base_revision,
                    temporary_context=slow_temporary,
                    state=slow_state,
                )
            )
        finally:
            await plugin.stop_consumers()

        committed = smoke_context.conversation_contexts.get(key=key)
        assert committed is not None
        self.assertEqual(
            [message.text for message in committed.messages_lst],
            [
                "角色、知识库和通用群聊要求",
                "快速请求生成的摘要和正文",
                "快速回复",
                "慢速用户消息",
                "慢速回复",
            ],
        )

    async def test_prompt_change_resets_only_affected_group_context(self) -> None:
        """提示词变化替换上下文，普通 token 预算变化保留既有历史。"""
        manager = FakeConfigManager(build_snapshot())
        plugin = AIGroupChatPlugin(
            context=cast(Context, SmokeContext()),
            plugin_config=ai_plugin_config(manager),
        )
        try:
            first_runtime = plugin._current_runtime()  # pyright: ignore[reportPrivateUsage]
            self.assertIsNotNone(first_runtime)
            assert first_runtime is not None
            first_context = plugin._get_group_context(  # pyright: ignore[reportPrivateUsage]
                runtime=first_runtime,
                group=first_runtime.groups["40000"],
                key=conversation_key(),
            )
            first_context.add_msg(ChatMessage(role="user", text="保留的历史"))

            manager.plugins = build_snapshot(
                revision=2,
                max_context_tokens=500_000,
            )
            budget_runtime = plugin._current_runtime()  # pyright: ignore[reportPrivateUsage]
            assert budget_runtime is not None
            budget_context = plugin._get_group_context(  # pyright: ignore[reportPrivateUsage]
                runtime=budget_runtime,
                group=budget_runtime.groups["40000"],
                key=conversation_key(),
            )
            self.assertIs(budget_context, first_context)
            self.assertEqual(len(budget_context.messages_lst), 2)
            self.assertEqual(budget_context.max_context_tokens, 500_000)

            manager.plugins = build_snapshot(
                revision=3,
                system_prompt="新的角色、知识库和通用群聊要求",
                max_context_tokens=500_000,
            )
            prompt_runtime = plugin._current_runtime()  # pyright: ignore[reportPrivateUsage]
            assert prompt_runtime is not None
            prompt_context = plugin._get_group_context(  # pyright: ignore[reportPrivateUsage]
                runtime=prompt_runtime,
                group=prompt_runtime.groups["40000"],
                key=conversation_key(),
            )
            self.assertIsNot(prompt_context, first_context)
            self.assertEqual(len(prompt_context.messages_lst), 1)
            self.assertEqual(
                prompt_context.messages_lst[0].text,
                "新的角色、知识库和通用群聊要求",
            )
        finally:
            await plugin.stop_consumers()

    async def test_active_request_keeps_old_runtime_and_next_uses_new_runtime(
        self,
    ) -> None:
        """运行中的请求不被配置替换，随后请求读取新模型配置。"""
        manager = FakeConfigManager(build_snapshot())
        smoke_context = SmokeContext()
        smoke_context.llm.formal_release = asyncio.Event()
        plugin = AIGroupChatPlugin(
            context=cast(Context, smoke_context),
            plugin_config=ai_plugin_config(manager),
        )
        first = asyncio.create_task(plugin.run(build_event("30003")))
        try:
            await asyncio.wait_for(smoke_context.llm.formal_entered.wait(), timeout=1)
            manager.plugins = build_snapshot(
                revision=2,
                model_name="new-main-model",
                system_prompt="请求完成后生效的新提示词",
            )
            smoke_context.llm.formal_release.set()
            self.assertTrue(await first)
            self.assertIsNone(
                smoke_context.conversation_contexts.get(key=conversation_key())
            )
            self.assertTrue(await plugin.run(build_event("30004")))
        finally:
            smoke_context.llm.formal_release.set()
            if not first.done():
                await first
            await plugin.stop_consumers()

        self.assertEqual(
            smoke_context.llm.formal_models,
            [
                ("main-vendor", "main-model"),
                ("main-vendor", "new-main-model"),
            ],
        )

    async def test_new_websocket_session_reuses_process_context(self) -> None:
        """新插件会话继续使用同一进程内已经提交的 assistant 历史。"""
        shared_contexts = ConversationContextStore()
        first_context = SmokeContext(conversation_contexts=shared_contexts)
        first_plugin = AIGroupChatPlugin(
            context=cast(Context, first_context),
            plugin_config=ai_plugin_config(FakeConfigManager(build_snapshot())),
        )
        try:
            self.assertTrue(await first_plugin.run(build_event("30007")))
        finally:
            await first_plugin.stop_consumers()

        second_context = SmokeContext(conversation_contexts=shared_contexts)
        second_plugin = AIGroupChatPlugin(
            context=cast(Context, second_context),
            plugin_config=ai_plugin_config(FakeConfigManager(build_snapshot())),
        )
        try:
            self.assertTrue(await second_plugin.run(build_event("30008")))
        finally:
            await second_plugin.stop_consumers()

        second_request = second_context.llm.formal_messages[0]
        self.assertTrue(
            any(
                message.role == "assistant"
                and message.text == "图片里写着测试成功。"
                for message in second_request
            )
        )

    async def test_process_context_is_scoped_by_bot(self) -> None:
        """同群号在不同机器人下不会读取彼此的进程内上下文。"""
        shared_contexts = ConversationContextStore()
        first_context = SmokeContext(
            bot_id="10000", conversation_contexts=shared_contexts
        )
        first_plugin = AIGroupChatPlugin(
            context=cast(Context, first_context),
            plugin_config=ai_plugin_config(FakeConfigManager(build_snapshot())),
        )
        try:
            self.assertTrue(
                await first_plugin.run(build_event("30009", bot_id="10000"))
            )
        finally:
            await first_plugin.stop_consumers()

        second_context = SmokeContext(
            bot_id="10001", conversation_contexts=shared_contexts
        )
        second_plugin = AIGroupChatPlugin(
            context=cast(Context, second_context),
            plugin_config=ai_plugin_config(FakeConfigManager(build_snapshot())),
        )
        try:
            self.assertTrue(
                await second_plugin.run(build_event("30010", bot_id="10001"))
            )
        finally:
            await second_plugin.stop_consumers()

        self.assertFalse(
            any(
                message.role == "assistant"
                for message in second_context.llm.formal_messages[0]
            )
        )


if __name__ == "__main__":
    unittest.main()
