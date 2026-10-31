"""跨唤醒会话连续性、协议闭合和有界内存行为。"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
import pytest
from test_harness_core import Empty, FakeHub, make_store
from test_provider_context import answer

from qq_agent_harness.contracts import (
    ConversationScope,
    JsonObject,
    Message,
    ToolDefinition,
    ToolResult,
    json_binding,
    model_binding,
    schema,
)
from qq_agent_harness.napcat.arguments import ReadMedia
from qq_agent_harness.napcat.media import MediaBatch
from qq_agent_harness.prompts import SILENT_RESPONSE
from qq_agent_harness.providers import ModelSession, current_time_text
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.runtime import AgentRuntime
from qq_agent_harness.settings import ParameterRule


def tool_response(protocol, *identifiers):
    if protocol == "chat_completions":
        return {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": identifier,
                                "type": "function",
                                "function": {
                                    "name": "qq__send_message",
                                    "arguments": "{}",
                                },
                            }
                            for identifier in identifiers
                        ],
                    },
                }
            ],
        }
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": identifier,
                "name": "qq__send_message",
                "arguments": "{}",
            }
            for identifier in identifiers
        ],
    }


def message(
    identifier, peer="person", kind: Literal["group", "private"] = "private", sender=None, bot="bot"
):
    return Message(
        scope=ConversationScope(bot_id=bot, kind=kind, peer_id=peer),
        message_id=identifier,
        sender_id=sender or peer,
        segments=[
            {"type": "at", "data": {"qq": bot}},
            {"type": "text", "data": {"text": "INPUT_" + identifier}},
        ],
    )


async def wake(runtime, item):
    await runtime.receive(item)
    await asyncio.gather(*list(runtime.workers.values()))


def sessions(runtime: AgentRuntime) -> list[JsonObject]:
    values = runtime.snapshot()["sessions"]
    assert isinstance(values, list)
    result: list[JsonObject] = []
    for value in values:
        assert isinstance(value, dict)
        result.append(value)
    return result


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_same_group_reuses_history_and_tool_results_but_resets_turn_limits(
    tmp_path, protocol, monkeypatch
):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    store.settings.runtime.max_model_rounds = 2
    store.settings.groups["a"].policy.max_sends_per_turn = 1
    registry = ToolRegistry(lambda: store.settings)
    bodies, sent = [], []
    tz = timezone(timedelta(hours=8))
    base_time = datetime(2026, 11, 1, 0, 27, 57, tzinfo=tz)
    assert current_time_text(base_time) == "2026-11-01 00:27:57 UTC+08:00 星期日"
    tick = 0

    def fake_time():
        nonlocal tick
        tick += 1
        return current_time_text(base_time + timedelta(seconds=tick))

    monkeypatch.setattr("qq_agent_harness.providers.current_time_text", fake_time)

    async def send(context, _arguments):
        sent.append(context.turn_id)
        result: JsonObject = {"message_id": f"sent-{len(sent)}", "fact": "TOOL_RESULT_ONLY"}
        context.sent_messages.append({"action": "send_group_msg", "status": "ok", "data": result})
        return ToolResult.success(result)

    registry.add(
        ToolDefinition(
            "qq__send_message",
            "发送",
            "messages.send",
            model_binding(Empty, send),
            effects=True,
        )
    )

    def respond(request):
        bodies.append(json.loads(request.content))
        index = len(bodies)
        payload = (
            tool_response(protocol, f"call-{index}")
            if index in (1, 3)
            else answer(protocol, "PRIVATE_ASSISTANT_FACT")
        )
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("first", "a", "group", "member-one"))
            first = sessions(runtime)[0]
            assert not runtime.workers
            await wake(runtime, message("second", "a", "group", "member-two"))
            second = sessions(runtime)[0]
            assert first["session_id"] == second["session_id"] and second["turns"] == 2
            assert len(sent) == 2 and sent[0] != sent[1]
            resumed = json.dumps(bodies[2], ensure_ascii=False)
            assert all(
                value in resumed
                for value in (
                    "INPUT_first",
                    "INPUT_second",
                    "TOOL_RESULT_ONLY",
                    "PRIVATE_ASSISTANT_FACT",
                    "sent-1",
                )
            )
            field = "messages" if protocol == "chat_completions" else "input"
            assert '"send_attempts": 0' in bodies[2][field][-1]["content"]
            # 当前时间只追加在每次请求最末尾的运行器状态项中，不写入固定前缀，保证跨步与跨轮缓存命中。
            for body in bodies[:4]:
                assert "当前时间：2026-11-01 00:2" in body[field][-1]["content"]
                assert "当前时间：" not in json.dumps(body[field][:-1], ensure_ascii=False)
            assert bodies[1][field][: len(bodies[0][field]) - 1] == bodies[0][field][:-1]
            assert bodies[2][field][: len(bodies[1][field]) - 1] == bodies[1][field][:-1]
            context = second["context"]
            assert isinstance(context, dict) and context["request_count"] == 2
            # 同一私聊对象不同机器人、同机器人不同私聊、群聊均不能串上下文。
            for item in (
                message("third", "p"),
                message("fourth", "q"),
                message("fifth", "p", bot="other"),
            ):
                await wake(runtime, item)
                fresh = json.dumps(bodies[-1])
                assert "INPUT_first" not in fresh and "TOOL_RESULT_ONLY" not in fresh
            assert len(runtime.sessions) == 4
            # 超过入口去重窗口后重投相同消息，也不能再次唤醒已处理过的会话。
            runtime.seen.clear()
            count = len(bodies)
            await wake(runtime, message("second", "a", "group", "member-two"))
            assert len(bodies) == count
        finally:
            await runtime.close()
        assert not runtime.sessions


@pytest.mark.parametrize("text", ["完成", SILENT_RESPONSE], ids=["text", "silent"])
async def test_chat_null_optional_fields_finish_after_send_and_survive_next_wake(tmp_path, text):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    registry = ToolRegistry(lambda: store.settings)
    bodies, sent = [], []

    async def send(context, _arguments):
        sent.append(context.turn_id)
        receipt: JsonObject = {
            "action": "send_group_msg",
            "status": "ok",
            "data": {"message_id": "sent-1"},
        }
        context.sent_messages.append(receipt)
        return ToolResult.success(receipt)

    registry.add(
        ToolDefinition(
            "qq__send_message",
            "发送",
            "messages.send",
            json_binding(schema(Empty), send),
            effects=True,
        )
    )

    def respond(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            data = tool_response("chat_completions", "send-once")
        else:
            data = answer("chat_completions", text)
            data["choices"][0]["message"]["tool_calls"] = None
        data["choices"][0]["message"].update(
            refusal=None, reasoning_content=None, reasoning_details=None
        )
        return httpx.Response(200, json={**data, "usage": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("first", "a", "group"))
            assert sessions(runtime)[0]["last_outcome"] == "completed"
            assert len(sent) == 1 and len(bodies) == 2
            await wake(runtime, message("second", "a", "group"))
            assert sessions(runtime)[0]["last_outcome"] == "silent"
            assert len(sent) == 1 and len(bodies) == 3
            assistants = [item for item in bodies[2]["messages"] if item.get("role") == "assistant"]
            assert assistants == [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "send-once",
                            "type": "function",
                            "function": {"name": "qq__send_message", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "assistant", "content": text},
            ]
            results = [item for item in bodies[2]["messages"] if item.get("role") == "tool"]
            assert len(results) == 1 and json.loads(results[0]["content"])["status"] == "ok"
        finally:
            await runtime.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_only_new_history_is_added_and_persona_updates_without_losing_session(
    tmp_path, protocol
):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    store.settings.runtime.history_count = 60
    (tmp_path / "knowledge.md").write_text("KNOWLEDGE_ONE", encoding="utf-8")
    store.settings.knowledge_files = ["knowledge.md"]
    registry = ToolRegistry(lambda: store.settings)

    async def read(_context, _arguments):
        return ToolResult.success([])

    registry.add(ToolDefinition("qq__get_history", "历史", "messages.read", json_binding({}, read)))

    class Hub(FakeHub):
        history = [message("older"), message("old"), message("old"), message("first")]

        async def get_history(self, scope, count):
            return self.history

    hub = Hub()
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=answer(protocol, "REPLY_ONE"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store, registry, hub, session_factory=lambda *args: ModelSession(*args, client=client)
        )
        try:
            await wake(runtime, message("first"))
            field = "messages" if protocol == "chat_completions" else "input"
            history_parts = [item for item in bodies[0][field] if "INPUT_older" in str(item)]
            assert len(history_parts) == 1
            history_data = json.loads(history_parts[0]["content"])
            assert [item["message_id"] for item in history_data["历史消息"]] == ["older", "old"]
            assert all("scope" not in item for item in history_data["历史消息"])
            detail = runtime.inspect(message("first").scope.key, since=None, session_id="")
            assert detail is not None and isinstance(entries := detail["entries"], list)
            assert [
                historical["message_id"]
                for item in entries
                if isinstance(item, dict)
                and item.get("kind") == "history"
                and isinstance(historical := item.get("message"), dict)
            ] == ["older", "old"]
            identifier = sessions(runtime)[0]["session_id"]
            hub.history = [message("old"), message("first"), message("new-history")]
            (tmp_path / "persona.md").write_text("UPDATED_PERSONA", encoding="utf-8")
            await wake(runtime, message("second"))
            serialized = json.dumps(bodies[-1])
            assert serialized.count('INPUT_old\\"') == 1
            assert serialized.count("INPUT_older") == 1
            assert serialized.count("INPUT_first") == 1
            assert serialized.count("INPUT_new-history") == 1
            assert serialized.count("KNOWLEDGE_ONE") == 1
            assert bodies[-1][field][0]["content"].endswith("UPDATED_PERSONA")
            assert sessions(runtime)[0]["session_id"] == identifier
            parts = bodies[-1][field]
            previous = next(
                i
                for i, item in enumerate(parts)
                if item.get("type") == "message" or item.get("role") == "assistant"
            )
            new_history = next(
                i for i, item in enumerate(parts) if "INPUT_new-history" in str(item)
            )
            assert new_history > previous
        finally:
            await runtime.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_all_authorized_tools_are_stable_across_rounds_and_wakes_until_revoked(
    tmp_path, protocol
):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    store.settings.runtime.max_model_rounds = 2
    registry = ToolRegistry(lambda: store.settings)
    names = [f"lookup__item_{index:02d}" for index in range(16)]
    dispatched = []

    async def read(context, _arguments):
        dispatched.append(context.turn_id)
        return ToolResult.success({"found": True})

    for name in reversed(names):
        registry.add(
            ToolDefinition(name, "查询", "messages.read", json_binding(schema(Empty), read))
        )
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        index = len(bodies)
        if index in (1, 3, 5):
            name = names[{1: 0, 3: 15, 5: 3}[index]]
            payload = tool_response(protocol, f"call-{index}")
            if protocol == "responses":
                payload["output"][0]["name"] = name
            else:
                payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = name
        else:
            payload = answer(protocol, "完成")
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("first"))
            await wake(runtime, message("second"))
            definitions = bodies[0]["tools"]
            assert all(body["tools"] == definitions for body in bodies[:4])
            assert [
                (tool if protocol == "responses" else tool["function"])["name"]
                for tool in definitions
            ] == names
            store.settings.private.policy.tools[names[3]] = False
            detail = runtime.inspect(message("first").scope.key, since=None, session_id="")
            assert detail is not None and detail["available_tools"] == [
                name for name in names if name != names[3]
            ]
            await wake(runtime, message("third"))
            resumed = {
                (tool if protocol == "responses" else tool["function"])["name"]
                for tool in bodies[4]["tools"]
            }
            assert resumed == set(names) - {names[3]}
            assert len(dispatched) == 2
            assert all(item["last_outcome"] == "silent" for item in sessions(runtime))
        finally:
            await runtime.close()


async def test_automatic_message_media_obeys_the_same_source_restrictions_as_tool_calls(tmp_path):
    store = make_store(tmp_path)
    policy = store.settings.private.policy
    policy.tools["qq__read_media"] = True
    policy.parameters["qq__read_media"] = {"user_id": ParameterRule(allowed=["20"])}
    registry = ToolRegistry(lambda: store.settings)
    selected = []

    async def read(_context, _arguments):
        raise AssertionError("模型不应主动读取媒体")

    registry.add(
        ToolDefinition("qq__read_media", "媒体", "media.read", model_binding(ReadMedia, read))
    )

    async def read_media(segments, _connection) -> MediaBatch:
        selected.append(segments)
        return MediaBatch()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=answer("responses")))
    ) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        runtime.read_media = read_media
        picture = {"type": "image", "data": {"url": "https://gchat.qpic.cn/image"}}
        try:
            first = message("first")
            first.segments.append(picture)
            await wake(runtime, first)
            assert selected == [[]]
            policy.parameters["qq__read_media"]["user_id"].allowed = [None, "20"]
            second = message("second")
            second.segments.append(picture)
            await wake(runtime, second)
            assert selected == [[], [picture]]
        finally:
            await runtime.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_cancel_closes_all_calls_and_unknown_effect_is_not_replayed_on_next_wake(
    tmp_path, protocol
):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    registry = ToolRegistry(lambda: store.settings)
    entered = asyncio.Event()
    dispatched, bodies = [], []

    async def blocked(context, _arguments):
        context.effect_submitted = True
        dispatched.append(context.turn_id)
        entered.set()
        await asyncio.Event().wait()
        return ToolResult.success()

    registry.add(
        ToolDefinition(
            "qq__send_message",
            "发送",
            "messages.send",
            model_binding(Empty, blocked),
            effects=True,
        )
    )

    def respond(request):
        bodies.append(json.loads(request.content))
        count = len(bodies)
        payload = (
            tool_response(protocol, "interrupted", "not-started")
            if count == 1
            else tool_response(protocol, f"retry-{count}")
            if count in (2, 4)
            else answer(protocol)
        )
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            first = message("first")
            await runtime.receive(first)
            await asyncio.wait_for(entered.wait(), 2)
            assert await runtime.cancel(first.scope.key)
            await wake(runtime, message("second"))
            assert len(dispatched) == 1
            field = "messages" if protocol == "chat_completions" else "input"
            outputs = [
                item
                for item in bodies[1][field]
                if item.get("role") == "tool" or item.get("type") == "function_call_output"
            ]
            statuses = [
                json.loads(item.get("content", item.get("output")))["status"] for item in outputs
            ]
            assert statuses == ["unknown", "cancelled"]
            assert "unknown" in json.dumps(bodies[2])
            # 已披露的目录不替代执行时授权；撤权后模型猜工具名也不能调用。
            store.settings.private.policy.tools["qq__send_message"] = False
            await wake(runtime, message("third"))
            assert len(dispatched) == 1
            assert "PermissionDenied" in json.dumps(bodies[4])
            names = [tool.get("function", tool)["name"] for tool in bodies[3]["tools"]]
            assert "qq__send_message" not in names
        finally:
            await runtime.close()


async def test_idle_lru_capacity_and_shutdown_release_retained_sessions(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.settings.runtime.max_retained_sessions = 2
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            ToolRegistry(lambda: store.settings),
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("a1", "a"))
            await wake(runtime, message("b1", "b"))
            await wake(runtime, message("a2", "a"))
            await wake(runtime, message("c1", "c"))
            assert set(runtime.sessions) == {"bot:private:a", "bot:private:c"}
            assert any(
                item["kind"] == "session" and item["scope_key"] == "bot:private:b"
                for item in runtime.activity
            )
            await wake(runtime, message("b2", "b"))
            assert "INPUT_b1" not in json.dumps(bodies[-1])
        finally:
            await runtime.close()
        assert runtime.snapshot()["sessions"] == []


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_previous_turn_input_is_compactable_and_summary_survives_next_wake(protocol):
    from qq_agent_harness.settings import ModelEndpoint

    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        text = "SUMMARY_OF_FIRST_TURN" if "tools" not in body else "VISIBLE_FACT_" * 230
        return httpx.Response(200, json=answer(protocol, text))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(protocol=protocol, model="test", max_context_tokens=4096),
            "SYSTEM",
            "FIRST_INPUT_" * 300,
            client=client,
        )
        await session.step([])
        session.end_turn("completed")
        session.begin_turn("SECOND_INPUT")
        await session.step([])
        compactions = session.context_state["compactions"]
        assert isinstance(compactions, int) and compactions >= 1
        assert any("FIRST_INPUT_" in json.dumps(body) for body in bodies if "tools" not in body)
        assert "SECOND_INPUT" in json.dumps(bodies[-1])
        session.end_turn("completed")
        session.begin_turn("THIRD_INPUT")
        await session.step([])
        assert "SUMMARY_OF_FIRST_TURN" in json.dumps(bodies[-1])
        assert "THIRD_INPUT" in json.dumps(bodies[-1])


async def test_protocol_switch_preserves_visible_facts_without_old_wire_objects():
    from qq_agent_harness.settings import ModelEndpoint

    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(
            200,
            json=answer("chat_completions" if "messages" in body else "responses", "KNOWN_FACT"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(protocol="responses", model="one"),
            "SYSTEM",
            "FIRST_INPUT",
            client=client,
        )
        await session.step([])
        session.end_turn("completed")
        await session.configure(
            ModelEndpoint(protocol="chat_completions", model="two"), "NEW_SYSTEM"
        )
        session.begin_turn("SECOND_INPUT")
        await session.step([])
        assert "input" not in bodies[-1]
        assert all(
            value in json.dumps(bodies[-1])
            for value in ("KNOWN_FACT", "FIRST_INPUT", "SECOND_INPUT")
        )
        assert bodies[-1]["messages"][0]["content"] == "NEW_SYSTEM"


async def test_completed_results_survive_model_failure_between_wakes(tmp_path):
    store = make_store(tmp_path)
    store.settings.providers["test"].max_retries = 0
    registry = ToolRegistry(lambda: store.settings)
    dispatched, bodies = [], []

    async def lookup(context, _arguments):
        dispatched.append(context.turn_id)
        return ToolResult.success({"fact": f"LOOKUP_RESULT_{len(dispatched)}"})

    registry.add(
        ToolDefinition("qq__lookup", "查询", "messages.read", json_binding(schema(Empty), lookup))
    )

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        index = len(bodies)
        if index == 4:
            return httpx.Response(503)
        if index in (1, 3):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": f"call-{index}",
                            "name": "qq__lookup",
                            "arguments": "{}",
                        }
                    ],
                },
            )
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("first"))
            identifier = sessions(runtime)[0]["session_id"]
            await wake(runtime, message("second"))
            assert sessions(runtime)[0]["last_outcome"] == "error"
            await wake(runtime, message("third"))
            assert sessions(runtime)[0]["session_id"] == identifier
            assert len(dispatched) == 2
            assert "qq__lookup" in [tool["name"] for tool in bodies[0]["tools"]]
            assert all(
                text in json.dumps(bodies[-1])
                for text in ("LOOKUP_RESULT_1", "LOOKUP_RESULT_2", "INPUT_first", "INPUT_second")
            )
        finally:
            await runtime.close()


async def test_byte_budget_reclaims_oversized_idle_context(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.settings.runtime.max_retained_bytes = 1
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=answer("responses")),
        )
    ) as client:
        runtime = AgentRuntime(
            store,
            ToolRegistry(lambda: store.settings),
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("first"))
            assert sessions(runtime) == []
            assert any(item["kind"] == "session" for item in runtime.activity)
        finally:
            await runtime.close()


async def test_capacity_reclamation_never_closes_a_running_session(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.settings.runtime.max_retained_sessions = 1
    entered, gate = asyncio.Event(), asyncio.Event()

    async def respond(request):
        if "INPUT_wait" in request.content.decode():
            entered.set()
            await gate.wait()
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            ToolRegistry(lambda: store.settings),
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await runtime.receive(message("wait", "a"))
            await asyncio.wait_for(entered.wait(), 2)
            identifier = runtime.sessions["bot:private:a"].session_id
            await runtime.receive(message("fast", "b"))
            await runtime.workers["bot:private:b"]
            assert "bot:private:a" in runtime.sessions
            assert runtime.sessions["bot:private:a"].session_id == identifier
            gate.set()
            await asyncio.gather(*list(runtime.workers.values()))
            assert set(runtime.sessions) == {"bot:private:a"}
            assert sessions(runtime)[0]["last_outcome"] == "silent"
        finally:
            await runtime.close()
