"""正常使用被额外限额或失败扩散阻断的回归场景。"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, ListToolsResult, Tool, ToolAnnotations
from test_harness_core import make_store
from test_napcat import GROUP, Bot, message, registry
from test_provider_context import answer, call_answer
from test_sandbox import FakeEngine, FakeQuota, make_runner

from qq_agent_harness.context import ProviderError
from qq_agent_harness.contracts import (
    JSON_OBJECT,
    JsonObject,
    JsonValue,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from qq_agent_harness.mcp_tools import MCPManager
from qq_agent_harness.napcat.media import MediaReader
from qq_agent_harness.providers import ModelSession
from qq_agent_harness.runtime import ConversationSession
from qq_agent_harness.sandbox.client import RunnerClient
from qq_agent_harness.sandbox.egress import PublicProxy
from qq_agent_harness.sandbox.runner import ExecuteRequest
from qq_agent_harness.sandbox.runner import create_app as runner_app
from qq_agent_harness.sandbox.tools import build_tools as sandbox_tools
from qq_agent_harness.settings import (
    MCPSettings,
    ModelEndpoint,
    NapCatSettings,
    ParameterRule,
    SandboxSettings,
)
from qq_agent_harness.web import create_app


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
async def test_large_tool_result_keeps_tail_and_pagination_within_context(protocol):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json=call_answer(protocol, "read") if len(requests) == 1 else answer(protocol)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(protocol=protocol, model="test", max_context_tokens=500_000),
            "system",
            "input",
            client=client,
        )
        call = (await session.step([])).calls[0]
        result = ToolResult.success(
            {"body": "资料" * 30_000, "next_offset": 40, "tail": "COMPLETE_TAIL"}
        )
        session.result(call, result)
        await session.step([])
    items = requests[1]["messages" if protocol == "chat_completions" else "input"]
    output = next(
        item
        for item in items
        if item.get("role") == "tool" or item.get("type") == "function_call_output"
    )
    assert json.loads(output.get("content", output.get("output"))) == result.model_dump(mode="json")


@pytest.mark.parametrize("compressible", [False, True])
async def test_soft_budget_or_summary_failure_does_not_stop_fitting_request(compressible):
    normal, summaries = [], []

    def respond(request):
        body = json.loads(request.content)
        if "tools" not in body:
            summaries.append(body)
            return httpx.Response(503, json={"error": {"message": "PRIVATE_ERROR"}})
        normal.append(body)
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(model="test", max_context_tokens=10_000),
            "system",
            "wake" if compressible else "x" * 16_500,
            client=client,
        )
        if compressible:
            session.add_background("x" * 16_500)
        session.request_limit = 1
        await session.step([])
        assert session.model_request_count == 1
        assert session.compaction_request_count == int(compressible)
    assert len(normal) == 1 and len(summaries) == int(compressible)
    assert "x" * 16_500 in json.dumps(normal[0])


async def test_preflight_send_failure_preserves_budget_and_other_messages_can_follow_unknown():
    async def wrong_scope(_action: str, _params: JsonObject) -> JsonValue:
        return message(group="elsewhere")

    bot = Bot(wrong_scope)
    tools, context, _ = registry(bot)
    for number in range(5):
        failed = await tools.execute(
            context,
            "qq__send_message",
            {
                "parts": [
                    {"type": "reply", "message_id": str(number)},
                    {"type": "text", "text": "prepare"},
                ]
            },
        )
        assert failed.status == "error"
    assert context.send_attempts == 0

    async def uncertain(_action: str, _params: JsonObject) -> JsonValue:
        raise TimeoutError()

    bot.handler = uncertain
    first: JsonObject = {"parts": [{"type": "text", "text": "first"}]}
    assert (await tools.execute(context, "qq__send_message", first)).status == "unknown"
    bot.handler = None
    assert (await tools.execute(context, "qq__send_message", first)).status == "unknown"
    assert (
        await tools.execute(
            context, "qq__send_message", {"parts": [{"type": "text", "text": "new" * 5000}]}
        )
    ).status == "ok"
    assert context.send_attempts == 2
    assert len([name for name, _ in bot.calls if name == "send_group_msg"]) == 2


async def test_non_qq_data_and_loading_more_than_twelve_tools_remain_usable():
    tools, context, settings = registry(Bot())
    settings.groups["10"].policy.allow_at_all = False

    async def inspect(_context: ToolContext, arguments: JsonObject) -> ToolResult:
        return ToolResult.success(arguments)

    names = [f"inspect_{index}" for index in range(15)]
    for name in names:
        tools.add(ToolDefinition(name, "检查数据", "messages.read", {"type": "object"}, inspect))
    loaded = await tools.execute(
        context,
        "harness__load_tools",
        JSON_OBJECT.validate_python({"names": names, "replace": True}),
    )
    assert loaded.status == "ok"
    result = await tools.execute(context, names[0], {"sample": {"type": "at", "qq": "all"}})
    assert result.status == "ok"
    await tools.execute(context, "harness__load_tools", {"names": [names[0]], "replace": True})
    exposed = {str(item["name"]) for item in tools.definitions(context)}
    assert names[0] in exposed and names[1] not in exposed and "qq__send_message" in exposed


async def test_unknown_operation_console_resolution_does_not_replay_or_bypass_permission(tmp_path):
    store = make_store(tmp_path)
    app = create_app(store.path, connect_mcp=False)
    calls = []

    async def effect(context: ToolContext, arguments: JsonObject) -> ToolResult:
        context.effect_submitted = True
        calls.append(arguments)
        return ToolResult(status="unknown") if len(calls) == 1 else ToolResult.success()

    tool = ToolDefinition(
        "example_effect", "测试动作", "messages.send", {"type": "object"}, effect, effects=True
    )
    app.state.registry.add(tool)
    scope = GROUP.model_copy(update={"peer_id": "a"})
    context = ToolContext(scope, "turn", Bot())
    assert (await app.state.registry.execute(context, tool.name, {})).status == "unknown"
    retained = ConversationSession(None, scope, unknown_effects=context.unknown_effects)
    app.state.runtime.sessions[scope.key] = retained
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        before = (await client.get("/api/sessions/detail", params={"key": scope.key})).json()
        identifier = before["unknown_operations"][0]["id"]
        assert (await app.state.registry.execute(context, tool.name, {})).status == "unknown"
        resolved = await client.post(
            "/api/sessions/operations/resolve",
            json={"scope_key": scope.key, "operation_id": identifier, "executed": False},
        )
        assert resolved.status_code == 200 and len(calls) == 1
        app.state.store.settings.groups["a"].policy.tools[tool.name] = False
        assert (
            await app.state.registry.execute(context, tool.name, {})
        ).error_type == "PermissionDenied"
        app.state.store.settings.groups["a"].policy.tools[tool.name] = True
        assert (await app.state.registry.execute(context, tool.name, {})).status == "ok"
        assert len(calls) == 2
    await app.state.runtime.close()
    await app.state.runner.close()


async def test_mcp_bad_tool_and_individual_errors_leave_other_tools_available():
    tools, context, settings = registry(Bot())
    settings.groups["10"].policy.categories["mcp.fixture"] = True

    class Remote:
        async def list_tools(self, _cursor: str | None) -> ListToolsResult:
            return ListToolsResult(
                tools=[
                    Tool(
                        name="read",
                        inputSchema={"type": "object"},
                        annotations=ToolAnnotations(readOnlyHint=True),
                    ),
                    Tool(name="bad", inputSchema={"$ref": "https://invalid.example/schema"}),
                ]
            )

        async def call_tool(
            self, _name: str, arguments: JsonObject, **_options: object
        ) -> CallToolResult:
            if arguments.get("mode") == "reject":
                raise McpError(ErrorData(code=-32602, message="PRIVATE_ERROR"))
            if arguments.get("mode") == "timeout":
                raise McpError(ErrorData(code=408, message="PRIVATE_ERROR"))
            if arguments.get("mode") == "server":
                raise McpError(ErrorData(code=-32000, message="PRIVATE_ERROR"))
            if arguments.get("mode") == "read_timeout":
                raise httpx.ReadTimeout("PRIVATE_ERROR")
            return CallToolResult(content=[])

    manager = MCPManager({}, tools)
    disconnected = asyncio.Event()

    def disconnect() -> None:
        disconnected.set()
        manager._withdraw("fixture")

    await manager._refresh(
        "fixture", MCPSettings(), cast(ClientSession, Remote()), disconnected, disconnect
    )
    assert "mcp__fixture__bad" not in tools.tools
    assert manager.statuses["fixture"]["excluded_tools"]
    for mode in ("reject", "timeout", "server", "read_timeout"):
        result = await tools.execute(context, "mcp__fixture__read", {"mode": mode})
        assert result.status == "error" and "PRIVATE_ERROR" not in result.message
        assert not disconnected.is_set() and not context.unknown_effects
    assert (await tools.execute(context, "mcp__fixture__read", {})).status == "ok"


async def test_media_selection_can_read_twenty_first_item_and_resume_page():
    async def remote(_action: str, _params: JsonObject) -> JsonValue:
        raw = message()
        raw["message"] = [
            {"type": "image", "data": {"file": f"{index}.png", "base64": "aGVsbG8="}}
            for index in range(21)
        ]
        return raw

    tools, context, _ = registry(Bot(remote))
    first = await tools.execute(context, "qq__read_media", {"message_id": "30", "limit": 20})
    assert isinstance(first.data, dict) and first.data["next_offset"] == 20
    for selection in ({"offset": 20}, {"indexes": [20]}):
        last = await tools.execute(
            context,
            "qq__read_media",
            JSON_OBJECT.validate_python({"message_id": "30", **selection}),
        )
        assert last.status == "ok" and isinstance(last.data, dict)
        assert last.data["total"] == 21 and last.data["next_offset"] is None
        media = last.data["media"]
        assert isinstance(media, list) and len(media) == 1
        assert isinstance(media[0], dict) and media[0]["index"] == 20


async def test_media_refresh_has_time_after_original_url_fails():
    class ExpiredURL(MediaReader):
        async def download(self, source: str) -> tuple[bytes, str]:
            await asyncio.sleep(0.02)
            raise httpx.ReadTimeout("expired URL")

    async def remote(action: str, _params: JsonObject) -> JsonValue:
        assert action == "get_image"
        return {"file_name": "a.png", "base64": "aGVsbG8="}

    reader = ExpiredURL(NapCatSettings(media_timeout=0.01))
    asset = await reader.read(
        {"type": "image", "data": {"url": "https://expired.example/x", "file": "native-id"}},
        Bot(remote),
    )
    assert asset.content == b"hello" and asset.source == "napcat_base64"


async def test_forward_page_after_two_hundred_nodes_is_readable():
    async def remote(action: str, _params: JsonObject) -> JsonValue:
        if action == "get_msg":
            raw = message()
            raw["message"] = [{"type": "forward", "data": {"id": "outer"}}]
            return raw
        return {"messages": [{"content": str(index)} for index in range(205)]}

    tools, context, _ = registry(Bot(remote))
    page = await tools.execute(
        context, "qq__get_forward_messages", {"message_id": "30", "offset": 200, "limit": 100}
    )
    assert isinstance(page.data, dict)
    assert page.data["messages"] == [{"content": str(index)} for index in range(200, 205)]
    assert page.data["total"] == 205 and page.data["next_offset"] is None


async def test_large_text_and_directory_continue_without_hiding_files(tmp_path):
    runner = make_runner(tmp_path)
    settings = SandboxSettings(enabled=True, runner_token="t" * 24)
    client = RunnerClient(
        lambda: settings,
        transport=httpx.ASGITransport(app=runner_app(runner, settings.runner_token)),
    )
    content = "中文abc" * 220_000
    try:
        await client.write_file("nested/big.txt", content)
        await client.write_file("nested/big.txt", "END", append=True)
        offset, chunks = 0, []
        while True:
            page = await client.read_text("nested/big.txt", offset, 65_537)
            chunks.append(str(page["content"]))
            next_offset = page["next_offset"]
            if next_offset is None:
                break
            assert isinstance(next_offset, int) and next_offset > offset
            offset = next_offset
        assert "".join(chunks) == content + "END"
        for index in range(1001):
            (tmp_path / f"{index:04}.txt").touch()
        page = await client.files("", 1000, 100)
        assert page["total"] == 1002 and page["next_offset"] is None
        assert len(cast(list[JsonValue], page["files"])) == 2
        runner.quota = FakeQuota(True)
        assert (await client.status())["available"] is False
        assert (await client.files("nested"))["total"] == 1
        await client.cleanup("selected", ["nested/big.txt"], True)
        assert not (tmp_path / "nested/big.txt").exists()
        await client.cleanup("selected", [f"{index:04}.txt" for index in range(1001)], True)
        assert (await client.files())["total"] == 1
    finally:
        await client.close()


async def test_queued_execution_cancel_does_not_start_it_and_reads_wait_for_active_work(tmp_path):
    (tmp_path / "ready.txt").write_text("ready", encoding="utf-8")
    engine = FakeEngine(block=True)
    runner = make_runner(tmp_path, engine=engine)
    runner.settings.max_pending_operations = 2
    settings = SandboxSettings(enabled=True, runner_token="t" * 24)
    client = RunnerClient(
        lambda: settings,
        transport=httpx.ASGITransport(app=runner_app(runner, settings.runner_token)),
    )
    running = asyncio.create_task(client.execute("first", owner="first"))
    await engine.started.wait()
    queued = asyncio.create_task(client.execute("second", owner="second"))
    read = asyncio.create_task(client.read_text("ready.txt"))
    try:
        async with asyncio.timeout(2):
            while len(runner.waiting) < 3:
                await asyncio.sleep(0)
            assert not read.done()
            with pytest.raises(httpx.HTTPStatusError) as overflow:
                await client.execute("overflow", owner="overflow")
            assert overflow.value.response.status_code == 429
            await client.cancel("second")
            assert (await queued)["status"] == "cancelled"
            assert len(engine.created) == 1
            await client.cancel("first")
            assert (await running)["status"] == "cancelled"
            assert (await read)["content"] == "ready"
        assert not runner.lock.locked() and not runner.waiting
    finally:
        await client.cancel()
        await asyncio.gather(running, queued, read, return_exceptions=True)
        await client.close()


async def test_log_tail_after_five_seconds_is_complete(tmp_path):
    class SlowLogs(FakeEngine):
        async def logs(self, container_id: str) -> AsyncIterator[bytes]:
            yield b"begin\n"
            await asyncio.sleep(5.1)
            yield b"TAIL\n"

    result = await make_runner(tmp_path, engine=SlowLogs(), timeout=7).execute(
        ExecuteRequest(command="fake")
    )
    assert result["output"] == "begin\nTAIL\n"
    assert result["output_complete"] is True and result["truncated"] is False


async def test_cancel_during_log_drain_releases_workspace_immediately(tmp_path):
    draining = asyncio.Event()

    class BlockedLogs(FakeEngine):
        async def logs(self, container_id: str) -> AsyncIterator[bytes]:
            yield b"partial"
            draining.set()
            await asyncio.Event().wait()

    engine = BlockedLogs()
    runner = make_runner(tmp_path, engine=engine, timeout=30)
    task = asyncio.create_task(runner.execute(ExecuteRequest(command="fake", owner="draining")))
    async with asyncio.timeout(2):
        await draining.wait()
        assert (await runner.status())["queued"] == 0
        await runner.cancel("draining")
        result = await task
    assert result["status"] == "cancelled" and result["output_complete"] is False
    assert len(engine.removed) == 1 and not runner.lock.locked()


async def test_proxy_address_failover_custom_port_and_active_stream_survive_idle_budget():
    attempts = []

    async def endpoint(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        for _ in range(4):
            writer.write(b"part\n")
            await writer.drain()
            await asyncio.sleep(0.04)
        writer.close()
        await writer.wait_closed()

    origin = await asyncio.start_server(endpoint, "127.0.0.1", 0)
    origin_port = origin.sockets[0].getsockname()[1]

    async def resolve(_host: str, _port: int) -> list[str]:
        return ["2001:4860:4860::8888", "8.8.8.8"]

    async def connect(ip: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        attempts.append((ip, port))
        if ":" in ip:
            raise OSError("unavailable route")
        return await asyncio.open_connection("127.0.0.1", origin_port)

    proxy = PublicProxy(resolve, connect, allowed_ports={8443}, idle_timeout=0.1)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    async with origin, server, asyncio.timeout(2):
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        writer.write(b"CONNECT public.example:8443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        assert await reader.read() == b"part\n" * 4
        writer.close()
        await writer.wait_closed()
    assert attempts == [("2001:4860:4860::8888", 8443), ("8.8.8.8", 8443)]


async def test_append_respects_existing_file_modification_policy(tmp_path):
    (tmp_path / "keep.txt").write_text("before", encoding="utf-8")
    tools, context, settings = registry(Bot())
    settings.sandbox.enabled = True
    settings.sandbox.runner_token = "t" * 24
    policy = settings.groups["10"].policy
    policy.categories["sandbox.files"] = True
    rule = ParameterRule(allowed=[False])
    policy.parameters["sandbox__write_file"] = {"overwrite": rule}
    client = RunnerClient(
        lambda: settings.sandbox,
        transport=httpx.ASGITransport(
            app=runner_app(make_runner(tmp_path), settings.sandbox.runner_token)
        ),
    )
    for tool in sandbox_tools(lambda: settings.sandbox, client):
        tools.add(tool)
    arguments: JsonObject = {"path": "keep.txt", "content": "after", "append": True}
    try:
        result = await tools.execute(context, "sandbox__write_file", arguments)
        assert result.error_type == "PermissionDenied"
        assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "before"
        rule.allowed = [True]
        assert (await tools.execute(context, "sandbox__write_file", arguments)).status == "ok"
        assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "beforeafter"
    finally:
        await client.close()


async def test_provider_error_exposes_cause_without_echoing_upstream_body():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={
                    "error": {
                        "code": "unsupported_parameter",
                        "param": "reasoning_effort",
                        "message": "PRIVATE_SENTINEL",
                    }
                },
            )
        )
    ) as client:
        session = ModelSession(ModelEndpoint(model="test"), "system", "wake", client=client)
        with pytest.raises(ProviderError) as error:
            await session.step([])
    assert "unsupported_parameter" in str(error.value) and "reasoning_effort" in str(error.value)
    assert "PRIVATE_SENTINEL" not in str(error.value)
