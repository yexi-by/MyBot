"""会话上下文查看：可见内容投影、重置语义与接口映射。"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_harness_core import Empty, FakeHub, make_store
from test_provider_context import answer
from test_runtime_sessions import message, wake

from qq_agent_harness.contracts import (
    ConversationScope,
    JsonObject,
    JsonValue,
    Message,
    Model,
    ToolDefinition,
    ToolHandler,
    ToolResult,
    schema,
)
from qq_agent_harness.providers import ModelSession
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.runtime import AgentRuntime, ConversationSession, SessionBusy
from qq_agent_harness.session_view import project
from qq_agent_harness.settings import ModelEndpoint
from qq_agent_harness.web import create_app

REASONING = "PRIVATE_REASONING_TEXT"
MEDIA = "TUVESUFfUkFXX0RBVEE" * 32
INLINE = "SU5MSU5FX1NFR01FTlQ" * 16


class SendText(Model):
    text: str


def send_tool(handler: ToolHandler) -> ToolDefinition:
    return ToolDefinition(
        "qq__send_message",
        "发送",
        "messages.send",
        schema(SendText),
        handler,
        effects=True,
        model=SendText,
    )


async def unused(_context, _arguments) -> ToolResult:
    return ToolResult.success()


def send_call(protocol: str, identifier: str, text: str, *, reasoning: bool = False) -> JsonObject:
    arguments = json.dumps({"text": text})
    if protocol == "chat_completions":
        assistant: JsonObject = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": identifier,
                    "type": "function",
                    "function": {"name": "qq__send_message", "arguments": arguments},
                }
            ],
        }
        if reasoning:
            assistant["reasoning_content"] = REASONING
        return {"choices": [{"finish_reason": "tool_calls", "message": assistant}]}
    output: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": identifier,
            "name": "qq__send_message",
            "arguments": arguments,
        }
    ]
    if reasoning:
        output.insert(
            0,
            {
                "type": "reasoning",
                "id": "r-" + identifier,
                "encrypted_content": REASONING,
                "summary": [{"type": "summary_text", "text": REASONING}],
            },
        )
    return {"status": "completed", "output": output}


def view(runtime: AgentRuntime, key: str) -> tuple[JsonObject, list[JsonObject]]:
    detail = runtime.inspect(key, since=None, session_id="")
    assert detail is not None and isinstance(entries := detail["entries"], list)
    return detail, [entry for entry in entries if isinstance(entry, dict)]


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_view_follows_turns_without_reasoning_or_raw_media(tmp_path, protocol):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    registry = ToolRegistry(lambda: store.settings)
    bodies = []

    async def send(context, _arguments):
        receipt: JsonObject = {"message_id": "sent-1"}
        context.sent_messages.append({"action": "send_group_msg", "status": "ok", "data": receipt})
        return ToolResult.success(receipt)

    registry.add(send_tool(send))
    registry.add(ToolDefinition("qq__read_media", "媒体", "messages.read", schema(Empty), unused))

    def respond(request):
        bodies.append(json.loads(request.content))
        index = len(bodies)
        payload = (
            send_call(protocol, "call-1", "OUT_TEXT", reasoning=True)
            if index == 1
            else answer(protocol, "VISIBLE_REPLY" if index == 2 else "SECOND_REPLY")
        )
        return httpx.Response(200, json=payload)

    async def read_media(segments, _connection) -> JsonObject:
        image: JsonObject = {"type": "image", "mime_type": "image/png", "base64": MEDIA}
        return {"media": [image] if segments else []}

    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    pictured = Message(
        scope=scope,
        message_id="second",
        sender_id="member-two",
        sender_name="成员二",
        segments=[
            {"type": "at", "data": {"qq": "bot"}},
            {"type": "text", "data": {"text": "INPUT_second"}},
            {"type": "image", "data": {"file": "base64://" + INLINE}},
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        runtime.read_media = read_media
        try:
            await wake(runtime, message("first", "a", "group", "member-one"))
            await wake(runtime, pictured)
            detail, entries = view(runtime, scope.key)
            assert detail["turns"] == 2 and detail["state"] == "idle"
            assert [(entry["kind"], entry["turn"]) for entry in entries] == [
                ("system", 1),
                ("wake", 1),
                ("call", 1),
                ("assistant", 1),
                ("boundary", 1),
                ("wake", 2),
                ("media", 2),
                ("assistant", 2),
                ("boundary", 2),
            ]
            call, reply, boundary = entries[2], entries[3], entries[4]
            assert call["name"] == "qq__send_message" and call["arguments"] == {"text": "OUT_TEXT"}
            result = call["result"]
            assert isinstance(result, dict) and result["status"] == "ok"
            assert result["data"] == {"message_id": "sent-1"}
            assert reply["text"] == "VISIBLE_REPLY"
            assert boundary["outcome"] == "completed"
            assert boundary["sent_receipts"] == [
                {"action": "send_group_msg", "status": "ok", "message_id": "sent-1"}
            ]
            assert entries[6]["items"] == [
                {"type": "image", "mime": "image/png", "bytes": len(MEDIA) * 3 // 4}
            ]
            messages = entries[5]["messages"]
            assert isinstance(messages, list) and isinstance(woken := messages[0], dict)
            assert woken["sender_name"] == "成员二" and "scope" not in woken
            # 推理正文、模型媒体负载与消息段里的内嵌数据都不能出现在接口响应中。
            serialized = json.dumps(detail, ensure_ascii=False)
            assert REASONING not in serialized and MEDIA not in serialized
            assert INLINE not in serialized and "base64://[内嵌数据已省略]" in serialized
            # 同一会话、同一版本只返回摘要字段。
            version, session_id = detail["version"], detail["session_id"]
            assert isinstance(version, int) and isinstance(session_id, str)
            unchanged = runtime.inspect(scope.key, since=version, session_id=session_id)
            assert unchanged is not None and unchanged["entries"] is None
            renewed = runtime.inspect(scope.key, since=version, session_id="other")
            assert renewed is not None and isinstance(renewed["entries"], list)
        finally:
            await runtime.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_compacted_context_shows_summary_in_place_of_original_text(protocol):
    def respond(request):
        body = json.loads(request.content)
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
    entries = project(session)
    # 压缩后只剩系统提示、摘要、本轮输入与新输出；已移出的条目不能留下错配的标注。
    assert [(entry["kind"], entry["turn"]) for entry in entries] == [
        ("system", 1),
        ("summary", 2),
        ("other", 2),
        ("assistant", 2),
    ]
    summary = entries[1]["text"]
    assert isinstance(summary, str) and summary.startswith("SUMMARY_OF_FIRST_TURN")
    assert entries[2]["label"] == "唤醒输入" and entries[2]["text"] == "SECOND_INPUT"
    assert "FIRST_INPUT_" not in json.dumps(entries, ensure_ascii=False)


async def test_model_switch_shows_previous_context_expanded_without_reasoning():
    def respond(_request):
        payload = send_call("chat_completions", "call-1", "OUT_TEXT", reasoning=True)
        return httpx.Response(200, json=payload)

    first = json.dumps(
        {"当前唤醒消息": [{"segments": [{"type": "text", "data": {"text": "早上好"}}]}]},
        ensure_ascii=False,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(protocol="chat_completions", model="test"),
            "SYSTEM",
            first,
            client=client,
        )
        reply = await session.step([])
        session.result(reply.calls[0], ToolResult.success({"message_id": "sent-1"}))
        session.end_turn("completed")
        await session.configure(ModelEndpoint(protocol="responses", model="test"), "SYSTEM")
    entries = project(session)
    assert [entry["kind"] for entry in entries] == ["system", "transfer"]
    # 切换前的唤醒消息、调用参数和结果按展开后的结构显示，推理不随资料转入。
    text = entries[1]["text"]
    assert isinstance(text, str) and '"text": "早上好"' in text
    assert "OUT_TEXT" in text and "sent-1" in text
    assert REASONING not in json.dumps(entries, ensure_ascii=False)


async def test_reset_forgets_context_but_keeps_duplicate_guards(tmp_path):
    store = make_store(tmp_path)
    store.settings.runtime.history_count = 5
    registry = ToolRegistry(lambda: store.settings)
    entered = asyncio.Event()
    dispatched, bodies = [], []

    async def send(context, _arguments):
        context.effect_submitted = True
        dispatched.append(context.turn_id)
        entered.set()
        await asyncio.Event().wait()
        return ToolResult.success()

    registry.add(send_tool(send))
    registry.add(ToolDefinition("qq__get_history", "历史", "messages.read", schema(Empty), unused))

    class HistoryHub(FakeHub):
        async def get_history(self, scope: ConversationScope, count: int):
            return [
                Message(
                    scope=scope,
                    message_id="h1",
                    sender_id="someone",
                    segments=[{"type": "text", "data": {"text": "HISTORY_ONE"}}],
                )
            ]

    def respond(request):
        bodies.append(json.loads(request.content))
        index = len(bodies)
        payload = (
            send_call("responses", f"call-{index}", "SAME") if index <= 2 else answer("responses")
        )
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            HistoryHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            first = message("first")
            key = first.scope.key
            await runtime.receive(first)
            await asyncio.wait_for(entered.wait(), 2)
            assert await runtime.cancel(key)
            previous = runtime.sessions[key].session_id
            renewed = await runtime.reset(key, stop=False)
            assert renewed != previous
            detail, entries = view(runtime, key)
            assert detail["cleared"] is True and detail["turns"] == 0 and entries == []
            unknown = detail["unknown_operations"]
            assert isinstance(unknown, list) and len(unknown) == 1
            # 越过入口去重窗口后重投已处理的唤醒消息，重置后仍不会再处理。
            runtime.seen.clear()
            await wake(runtime, first)
            assert len(bodies) == 1
            await wake(runtime, message("second"))
            resumed = json.dumps(bodies[1], ensure_ascii=False)
            assert "HISTORY_ONE" in resumed and "INPUT_second" in resumed
            assert "INPUT_first" not in resumed
            # 新上下文里模型原样重发结果未知的动作，执行层仍然拦截。
            assert len(dispatched) == 1
            assert "此前相同操作结果未知" in json.dumps(bodies[2], ensure_ascii=False)
            detail, entries = view(runtime, key)
            assert detail["session_id"] == renewed and detail["turns"] == 1
            assert [entry["kind"] for entry in entries].count("history") == 1
        finally:
            await runtime.close()


async def test_running_session_is_reset_only_after_stopping(tmp_path):
    store = make_store(tmp_path)
    entered = asyncio.Event()

    async def respond(_request):
        entered.set()
        await asyncio.Event().wait()
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            ToolRegistry(lambda: store.settings),
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            slow = message("slow")
            key = slow.scope.key
            await runtime.receive(slow)
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(SessionBusy):
                await runtime.reset(key, stop=False)
            assert key in runtime.workers
            await runtime.reset(key, stop=True)
            assert key not in runtime.workers
            detail, entries = view(runtime, key)
            assert detail["cleared"] is True and entries == []
            assert runtime.activity[-1]["kind"] == "reset"
            with pytest.raises(KeyError):
                await runtime.reset("bot:private:missing", stop=True)
        finally:
            await runtime.close()


def test_session_detail_and_reset_api(tmp_path):
    store = make_store(tmp_path)
    scope = ConversationScope(bot_id="bot", kind="private", peer_id="p")
    woken = Message(
        scope=scope,
        message_id="m1",
        sender_id="p",
        segments=[{"type": "text", "data": {"text": "API_INPUT"}}],
    )
    app = create_app(store.path, connect_mcp=False)
    with TestClient(app) as client:
        model = ModelSession(
            ModelEndpoint(model="test"),
            "SYSTEM",
            json.dumps(
                {
                    "来源会话": scope.model_dump(),
                    "历史限制": "",
                    "当前唤醒消息": [woken.model_dump(mode="json")],
                },
                ensure_ascii=False,
            ),
        )
        model.end_turn("completed")
        app.state.runtime.sessions[scope.key] = ConversationSession(model, scope)
        detail = client.get("/api/sessions/detail", params={"key": scope.key}).json()
        assert [entry["kind"] for entry in detail["entries"]] == ["system", "wake", "boundary"]
        assert detail["entries"][1]["messages"][0]["segments"][0]["data"]["text"] == "API_INPUT"
        unchanged = client.get(
            "/api/sessions/detail",
            params={"key": scope.key, "since": detail["version"], "session": detail["session_id"]},
        ).json()
        assert unchanged["entries"] is None
        assert (
            client.get("/api/sessions/detail", params={"key": "bot:private:x"}).status_code == 404
        )
        reset = client.post("/api/sessions/reset", json={"scope_key": scope.key})
        assert reset.status_code == 200
        assert reset.json()["session_id"] != detail["session_id"]
        cleared = client.get("/api/sessions/detail", params={"key": scope.key}).json()
        assert cleared["cleared"] is True and cleared["entries"] == []
        missing = client.post("/api/sessions/reset", json={"scope_key": "bot:private:x"})
        assert missing.status_code == 404
        runtime = client.get("/api/runtime").json()
        assert runtime["capacity"]["sessions"] == 1 and runtime["sessions"][0]["cleared"] is True
