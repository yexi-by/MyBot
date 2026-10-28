"""模型请求失败恢复、取消和工具不重放的行为回归。"""

import asyncio
import json
import logging
from contextlib import suppress

import httpx
import pytest
from test_harness_core import Empty, FakeHub, make_store
from test_provider_context import answer, call_answer
from test_runtime_sessions import message, wake

from qq_agent_harness.contracts import JsonObject, ToolDefinition, ToolResult, schema
from qq_agent_harness.diagnostics import DiagnosticFormatter
from qq_agent_harness.prompts import SILENT_RESPONSE
from qq_agent_harness.providers import ModelSession, ProviderError
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.runtime import AgentRuntime
from qq_agent_harness.session_view import project
from qq_agent_harness.settings import ModelEndpoint


@pytest.fixture
def immediate_backoff(monkeypatch):
    monkeypatch.setattr("qq_agent_harness.providers.retry_delay", lambda *_: 0)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
@pytest.mark.parametrize(
    "failure",
    [
        503,
        504,
        429,
        "failed",
        "missing",
        "json",
        "read_timeout",
        "connection",
        "broken_response",
        "empty",
        "null",
        "whitespace",
        "empty_parts",
        "reasoning_only",
        "no_output",
    ],
)
async def test_transient_failure_retries_same_request_and_recovers(
    protocol, failure, immediate_backoff
):
    bodies = []
    events = []

    def respond(request):
        bodies.append(json.loads(request.content))
        if len(bodies) > 1:
            return httpx.Response(200, json=answer(protocol, "RECOVERED"))
        if isinstance(failure, int):
            return httpx.Response(failure, json={"error": {"message": "PRIVATE_ERROR"}})
        if failure == "failed":
            return httpx.Response(
                200,
                json={
                    **call_answer(protocol, "must-not-run"),
                    "status": "failed",
                    "error": {"message": "PRIVATE_ERROR"},
                },
            )
        if failure == "missing":
            return httpx.Response(200, json={"upstream_message": "PRIVATE_ERROR"})
        if failure == "json":
            return httpx.Response(200, text="PRIVATE_ERROR")
        if failure in ("empty", "null", "whitespace", "empty_parts", "reasoning_only", "no_output"):
            data = answer(protocol, " \n\t" if failure == "whitespace" else "")
            if protocol == "chat_completions":
                message = data["choices"][0]["message"]
                if failure == "null":
                    message["content"] = None
                elif failure == "empty_parts":
                    message["content"] = []
                elif failure == "reasoning_only":
                    message.update(content=None, reasoning_content="PRIVATE_REASONING")
                elif failure == "no_output":
                    del message["content"]
            elif failure == "null":
                data["output"][0]["content"][0]["text"] = None
            elif failure == "empty_parts":
                data["output"][0]["content"] = []
            elif failure == "reasoning_only":
                data["output"] = [
                    {"type": "reasoning", "encrypted_content": "PRIVATE_REASONING", "summary": []}
                ]
            elif failure == "no_output":
                data["output"] = []
            return httpx.Response(200, json=data)
        if failure == "read_timeout":
            raise httpx.ReadTimeout("PRIVATE_ERROR")
        if failure == "connection":
            raise httpx.ConnectError("PRIVATE_ERROR")
        raise httpx.RemoteProtocolError("PRIVATE_ERROR")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(protocol=protocol, model="test", max_retries=1),
            "SYSTEM",
            "INPUT",
            client=client,
        )
        session.request_limit = 1
        session.on_request_event = events.append
        result = await session.step([])
        assert result.text == "RECOVERED" and result.calls == []
        assert len(bodies) == 2 and bodies[0] == bodies[1]
        assert session.model_request_count == 1 and session.request_count == 2
        assert session.context_state["retry_count"] == 1
        assert [event["phase"] for event in events] == ["retry", "attempt", "recovered"]
        assert "PRIVATE_ERROR" not in json.dumps(events)
        assert "must-not-run" not in json.dumps(session.items)
        assert "PRIVATE_REASONING" not in json.dumps(session.items)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
@pytest.mark.parametrize("recover", [False, True])
async def test_empty_reply_after_search_recovers_or_fails_without_repeating_search(
    tmp_path, protocol, recover, immediate_backoff
):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    store.settings.providers["test"].max_retries = 1
    registry = ToolRegistry(lambda: store.settings)
    searched, sent, bodies = [], [], []

    async def lookup(context, _arguments):
        searched.append(context.turn_id)
        return ToolResult.success({"url": "https://example.com/FOUND_RESULT"})

    async def send(context, _arguments):
        sent.append(context.turn_id)
        receipt: JsonObject = {"message_id": "sent-once"}
        context.sent_messages.append({"action": "send_msg", "status": "ok", "data": receipt})
        return ToolResult.success(receipt)

    registry.add(ToolDefinition("lookup", "搜索", "messages.read", schema(Empty), lookup))
    registry.add(
        ToolDefinition(
            "qq__send_message", "发送", "messages.send", schema(Empty), send, effects=True
        )
    )

    def respond(request):
        bodies.append(json.loads(request.content))
        number = len(bodies)
        if number == 1:
            data = call_answer(protocol, "search")
        elif number == 2 or not recover:
            data = answer(protocol, "")
        elif number == 3:
            data = call_answer(protocol, "send")
            if protocol == "chat_completions":
                data["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = (
                    "qq__send_message"
                )
            else:
                data["output"][1]["name"] = "qq__send_message"
        else:
            data = answer(protocol, "完成")
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("search-request"))
            retained = runtime.sessions["bot:private:person"]
            assert len(searched) == 1 and len(sent) == int(recover)
            assert len(bodies) == (4 if recover else 3)
            assert bodies[1] == bodies[2] and "FOUND_RESULT" in json.dumps(bodies[2])
            assert retained.outcome == ("completed" if recover else "error")
            assert retained.model is not None and retained.model.retry_count == 1
            assert "FOUND_RESULT" in json.dumps(retained.model.items)
            if not recover:
                assert runtime.activity[-1]["kind"] == "error"
                assert not any(event.get("status") == "silent" for event in runtime.activity)
        finally:
            await runtime.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_explicit_silence_finishes_once_without_sending(tmp_path, protocol):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    attempts = 0

    def respond(_request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json=answer(protocol, SILENT_RESPONSE))

    hub = FakeHub()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            ToolRegistry(lambda: store.settings),
            hub,
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("unneeded"))
            retained = runtime.sessions["bot:private:person"]
            assert attempts == 1 and retained.outcome == "silent" and not hub.connection.calls
            assert retained.model is not None and retained.model.retry_count == 0
            assert runtime.activity[-1]["message"] == "模型明确选择本轮不发言"
            assert not any(event["kind"] == "model" for event in runtime.activity)
        finally:
            await runtime.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_explicit_refusal_is_visible_and_not_retried(protocol):
    data = answer(protocol, "")
    if protocol == "chat_completions":
        data["choices"][0]["message"]["refusal"] = "无法完成此请求"
    else:
        data["output"][0]["content"] = [{"type": "refusal", "refusal": "无法完成此请求"}]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        session = ModelSession(
            ModelEndpoint(protocol=protocol, model="test"), "system", "input", client=client
        )
        assert (await session.step([])).text == "无法完成此请求"
        assert session.request_count == 1 and session.retry_count == 0
        assert any(entry.get("text") == "无法完成此请求" for entry in project(session))


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
@pytest.mark.parametrize("idle", [False, True])
async def test_empty_summary_retries_atomically_with_its_selected_protocol(
    protocol, idle, immediate_backoff
):
    normal = 0
    summaries = 0
    events = []

    def respond(request):
        nonlocal normal, summaries
        body = json.loads(request.content)
        if "tools" not in body:
            summaries += 1
            return httpx.Response(
                200, json=answer(protocol, "" if summaries == 1 else "保留的背景事实")
            )
        normal += 1
        if normal == 1:
            return httpx.Response(400, json={"error": {"code": "context_length_exceeded"}})
        return httpx.Response(200, json=answer(protocol, "完成"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(
                protocol="chat_completions" if idle else protocol,
                model="test",
                max_context_tokens=4096,
                max_retries=1,
            ),
            "system",
            "input",
            client=client,
        )
        session.add_background("历史资料" * 300)
        session.on_request_event = events.append
        if idle:
            before, after = await session.compact_idle(
                ModelEndpoint(
                    protocol=protocol, model="summary", max_context_tokens=4096, max_retries=1
                ),
                request_limit=1,
            )
            assert after < before and normal == 0
        else:
            session.request_limit = 2
            assert (await session.step([])).text == "完成"
            assert session.model_request_count == 2 and session.compaction_request_count == 1
        assert summaries == 2 and "保留的背景事实" in json.dumps(session.items, ensure_ascii=False)
        assert events[0]["error_type"] == "empty_response"
        assert events[-1]["phase"] == "recovered"
        assert events[0]["request_kind"] == ("idle_compaction" if idle else "compaction")


@pytest.mark.parametrize("recover", [True, False])
async def test_default_retry_budget_allows_ten_retries_and_then_stops(recover, immediate_backoff):
    attempts = 0
    events = []

    def respond(_request):
        nonlocal attempts
        attempts += 1
        if recover and attempts == 11:
            return httpx.Response(200, json=answer("responses", "RECOVERED"))
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(ModelEndpoint(model="test"), "system", "input", client=client)
        session.request_limit = 1
        session.on_request_event = events.append
        if recover:
            assert (await session.step([])).text == "RECOVERED"
        else:
            original = list(session.items)
            with pytest.raises(ProviderError, match="已重试 10 次"):
                await session.step([])
            assert session.items == original
        assert attempts == 11 and session.model_request_count == 1
        assert session.request_count == 11 and session.retry_count == 10
        assert events[-1]["phase"] == ("recovered" if recover else "failed")
        assert events[-1]["attempt"] == 11


@pytest.mark.parametrize("maximum", [0, 2])
async def test_configured_retry_budget_is_enforced(maximum, immediate_backoff):
    attempts = 0

    def respond(_request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(504)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(model="test", max_retries=maximum), "system", "input", client=client
        )
        with pytest.raises(ProviderError, match="HTTP 504"):
            await session.step([])
        assert attempts == maximum + 1


@pytest.mark.parametrize(
    "status,payload",
    [
        (400, {"error": {"type": "invalid_request_error"}}),
        (401, {"error": {"type": "authentication_error"}}),
        (403, {"error": {"type": "permission_error"}}),
        (404, {"error": {"code": "model_not_found"}}),
        (429, {"error": {"code": "insufficient_quota"}}),
        (200, {"status": "failed", "error": {"code": "invalid_argument"}}),
    ],
)
async def test_permanent_error_does_not_retry(status, payload, immediate_backoff):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(ModelEndpoint(model="test"), "system", "input", client=client)
        with pytest.raises(ProviderError):
            await session.step([])
        assert len(requests) == 1 and session.retry_count == 0


@pytest.mark.parametrize("trickle", [False, True])
async def test_real_http_timeout_retries_with_fresh_request_deadline(trickle, immediate_backoff):
    requests = 0
    handlers = set()
    events = []

    async def handle(reader, writer):
        nonlocal requests
        task = asyncio.current_task()
        handlers.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            requests += 1
            body = json.dumps(answer("responses", "RECOVERED")).encode()
            if requests == 1 and not trickle:
                await reader.read()
                return
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n".encode()
            )
            if requests == 1:
                for byte in body:
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(0.02)
            else:
                writer.write(body)
                await writer.drain()
        except ConnectionError, asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        session = ModelSession(
            ModelEndpoint(
                base_url=f"http://127.0.0.1:{port}",
                api_key="synthetic",
                model="test",
                timeout=0.15,
                max_retries=1,
            ),
            "system",
            "input",
        )
        session.on_request_event = events.append
        try:
            async with asyncio.timeout(5):
                assert (await session.step([])).text == "RECOVERED"
            assert requests == 2
            assert events[0]["error_type"] == "RequestTimeout"
        finally:
            await session.close()
            if handlers:
                await asyncio.gather(*list(handlers))


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_retry_after_tool_result_does_not_repeat_completed_action(
    tmp_path, protocol, immediate_backoff, caplog
):
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = protocol
    store.settings.providers["test"].max_retries = 1
    store.settings.runtime.max_model_rounds = 2
    registry = ToolRegistry(lambda: store.settings)
    effects, bodies = [], []

    async def action(context, _arguments):
        effects.append(context.turn_id)
        return ToolResult.success({"receipt": "RECORDED"})

    registry.add(
        ToolDefinition("lookup", "测试动作", "messages.read", schema(Empty), action, effects=True)
    )

    def respond(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=call_answer(protocol, "completed"))
        if len(bodies) == 2:
            raise httpx.ReadTimeout("PRIVATE_ERROR")
        return httpx.Response(200, json=answer(protocol, "RECOVERED"))

    caplog.set_level(logging.INFO, logger="qq_agent_harness.runtime")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await wake(runtime, message("first"))
            assert len(effects) == 1 and len(bodies) == 3
            assert bodies[1] == bodies[2] and "RECORDED" in json.dumps(bodies[2])
            assert not any(event.get("kind") == "error" for event in runtime.activity)
            assert [event["status"] for event in runtime.activity if event["kind"] == "model"] == [
                "retrying",
                "completed",
            ]
            logs = [
                json.loads(DiagnosticFormatter().format(record))
                for record in caplog.records
                if record.getMessage().startswith("model_request_")
            ]
            assert logs[0]["error_type"] == "ReadTimeout" and logs[0]["retry"] == 1
            assert logs[-1]["message"] == "model_request_recovered"
            assert logs[-1]["scope_key"] == "bot:private:person"
            assert "PRIVATE_ERROR" not in json.dumps(logs)
        finally:
            await runtime.close()


@pytest.mark.parametrize("during_request", [False, True])
async def test_cancel_during_retry_stops_waiting_and_preserves_context(
    tmp_path, monkeypatch, during_request
):
    store = make_store(tmp_path)
    waiting, requested = asyncio.Event(), asyncio.Event()
    attempts = 0
    delay = 0 if during_request else 60
    original_event = AgentRuntime._model_request_event

    def request_event(self, key, event):
        original_event(self, key, event)
        if event["phase"] == "retry":
            assert event["delay_seconds"] == delay
            waiting.set()

    monkeypatch.setattr(AgentRuntime, "_model_request_event", request_event)

    async def respond(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": str(delay)})
        requested.set()
        await asyncio.Event().wait()
        raise AssertionError("取消后不得继续")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = AgentRuntime(
            store,
            ToolRegistry(lambda: store.settings),
            FakeHub(),
            session_factory=lambda *args: ModelSession(*args, client=client),
        )
        try:
            await runtime.receive(message("first"))
            async with asyncio.timeout(3):
                await (requested if during_request else waiting).wait()
                assert await runtime.cancel("bot:private:person")
            assert attempts == (2 if during_request else 1)
            retained = runtime.sessions["bot:private:person"]
            assert retained.outcome == "cancelled" and retained.model is not None
            assert "INPUT_first" in json.dumps(retained.model.items)
            assert not runtime.workers and not runtime.pending
        finally:
            await runtime.close()
