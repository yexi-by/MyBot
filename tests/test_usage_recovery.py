"""正常使用被额外限额或失败扩散阻断的回归场景。"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, ListToolsResult, Tool, ToolAnnotations
from test_napcat import Bot, message, registry
from test_provider_context import answer, call_answer

from qq_agent_harness.context import ProviderError
from qq_agent_harness.contracts import (
    JSON_OBJECT,
    JsonObject,
    JsonValue,
    ToolContext,
    ToolDefinition,
    ToolResult,
    json_binding,
)
from qq_agent_harness.mcp_tools import MCPManager
from qq_agent_harness.napcat.media import MediaReader
from qq_agent_harness.providers import ModelSession
from qq_agent_harness.settings import (
    MCPSettings,
    ModelEndpoint,
    NapCatSettings,
    ParameterRule,
    WorkspaceSettings,
)
from qq_agent_harness.workspace import (
    ExecuteRequest,
    WorkspaceOverloadedError,
    WorkspaceService,
)
from qq_agent_harness.workspace import build_tools as workspace_tools
from qq_agent_harness.workspace.executor import RunningProcess


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
            ModelEndpoint(model="test", max_context_tokens=10_000, max_retries=0),
            "system",
            "wake" if compressible else "x" * 16_500,
            client=client,
        )
        if compressible:
            session.add_background("x" * 16_500)
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
    # 同一轮重复提交相同的未确认发送请求仍被阻止，但发送新内容的消息可以正常执行。
    assert (await tools.execute(context, "qq__send_message", first)).status == "error"
    follow = await tools.execute(
        context, "qq__send_message", {"parts": [{"type": "text", "text": "阶段进展汇报"}]}
    )
    assert follow.status == "ok"
    next_turn = ToolContext(context.scope, "next-turn", bot)
    assert (
        await tools.execute(
            next_turn, "qq__send_message", {"parts": [{"type": "text", "text": "new" * 5000}]}
        )
    ).status == "ok"
    assert context.send_attempts == 2 and next_turn.send_attempts == 1
    assert len([name for name, _ in bot.calls if name == "send_group_msg"]) == 3


async def test_non_qq_data_and_more_than_twelve_tools_remain_usable():
    tools, context, settings = registry(Bot())
    settings.groups["10"].policy.allow_at_all = False

    async def inspect(_context: ToolContext, arguments: JsonObject) -> ToolResult:
        return ToolResult.success(arguments)

    names = [f"inspect_{index}" for index in range(15)]
    for name in names:
        tools.add(
            ToolDefinition(name, "检查数据", "messages", json_binding({"type": "object"}, inspect))
        )
    exposed = {str(item["name"]) for item in tools.definitions(context)}
    assert set(names) <= exposed and "qq__send_message" in exposed
    result = await tools.execute(context, names[0], {"sample": {"type": "at", "qq": "all"}})
    assert result.status == "ok"


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
        assert not disconnected.is_set()
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


async def test_large_text_and_directory_continue_without_hiding_files(tmp_path: Path):
    settings = WorkspaceSettings(enabled=True, root=str(tmp_path))
    service = WorkspaceService(lambda: settings)
    content = "中文abc" * 220_000
    await service.write_file("nested/big.txt", content)
    await service.write_file("nested/big.txt", "END", append=True)
    offset, chunks = 0, []
    while True:
        page = await service.read_text("nested/big.txt", offset, 65_537)
        chunks.append(str(page["content"]))
        next_offset = page["next_offset"]
        if next_offset is None:
            break
        assert isinstance(next_offset, int) and next_offset > offset
        offset = next_offset
    assert "".join(chunks) == content + "END"
    for index in range(1001):
        (tmp_path / f"{index:04}.txt").touch()
    page = await service.files("", 1000, 100)
    # 含 prepare 创建的 5 个系统目录 + nested + 1001 个文件 = 1007 项
    assert page["total"] == 1007 and page["next_offset"] is None
    assert len(cast(list[JsonValue], page["files"])) == 7
    # 超出空间预算时，status 标明 over_limit，但目录浏览与删除清理仍正常可用
    settings.max_bytes = 1_000_000
    st = await service.status()
    assert st["available"] is True and st["over_limit"] is True
    assert (await service.files("nested"))["total"] == 1
    await service.cleanup("selected", ["nested/big.txt"], True)
    assert not (tmp_path / "nested/big.txt").exists()
    await service.cleanup("selected", [f"{index:04}.txt" for index in range(1001)], True)
    assert (await service.files())["total"] == 6


async def test_queued_execution_cancel_does_not_start_it_and_reads_bypass_exec_lock(
    tmp_path: Path,
):
    (tmp_path / "ready.txt").write_text("ready", encoding="utf-8")
    started = asyncio.Event()
    spawned: list[str] = []

    class BlockedProc:
        def __init__(self) -> None:
            self.stopped = asyncio.Event()

        def output(self) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                if False:
                    yield b""
                await self.stopped.wait()

            return _gen()

        async def wait(self) -> int:
            await self.stopped.wait()
            return -9

        async def terminate(self) -> None:
            self.stopped.set()

    class BlockedSpawner:
        async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
            spawned.append(command)
            started.set()
            return BlockedProc()

    settings = WorkspaceSettings(
        enabled=True,
        root=str(tmp_path),
        concurrent_executions=1,
        max_pending_operations=2,
    )
    service = WorkspaceService(lambda: settings, spawner=BlockedSpawner())
    running = asyncio.create_task(service.execute(ExecuteRequest(command="first", owner="first")))
    await started.wait()
    queued = asyncio.create_task(service.execute(ExecuteRequest(command="second", owner="second")))
    queued_two = asyncio.create_task(
        service.execute(ExecuteRequest(command="third", owner="third"))
    )
    try:
        async with asyncio.timeout(2):
            while len(service.waiting) < 2:
                await asyncio.sleep(0)
            # 文件读取不等待活动执行锁，即使排队槽位已满也立即可读。
            assert (await service.read_text("ready.txt"))["content"] == "ready"
            with pytest.raises(WorkspaceOverloadedError):
                await service.execute(ExecuteRequest(command="overflow", owner="overflow"))
            await service.cancel("second")
            assert (await queued)["status"] == "cancelled"
            await service.cancel("third")
            assert (await queued_two)["status"] == "cancelled"
            assert spawned == ["first"]
            await service.cancel("first")
            assert (await running)["status"] == "cancelled"
        assert not service.is_busy and not service.waiting
    finally:
        await service.cancel()
        await asyncio.gather(running, queued, queued_two, return_exceptions=True)


async def test_log_tail_after_five_seconds_is_complete(tmp_path: Path):
    class SlowProc:
        def output(self) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                yield b"begin\n"
                await asyncio.sleep(5.1)
                yield b"TAIL\n"

            return _gen()

        async def wait(self) -> int:
            return 0

        async def terminate(self) -> None:
            return None

    class SlowSpawner:
        async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
            return SlowProc()

    settings = WorkspaceSettings(enabled=True, root=str(tmp_path), command_timeout=7)
    service = WorkspaceService(lambda: settings, spawner=SlowSpawner())
    result = await service.execute(ExecuteRequest(command="fake"))
    assert result["output"] == "begin\nTAIL\n"
    assert result["output_complete"] is True and result["truncated"] is False


async def test_cancel_during_log_drain_releases_workspace_immediately(tmp_path: Path):
    draining = asyncio.Event()
    terminated = False

    class DrainBlockedProc:
        def __init__(self) -> None:
            self.stopped = asyncio.Event()

        def output(self) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                yield b"partial"
                draining.set()
                await self.stopped.wait()

            return _gen()

        async def wait(self) -> int:
            return 0

        async def terminate(self) -> None:
            nonlocal terminated
            terminated = True
            self.stopped.set()

    class DrainBlockedSpawner:
        async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
            return DrainBlockedProc()

    settings = WorkspaceSettings(enabled=True, root=str(tmp_path), command_timeout=30)
    service = WorkspaceService(lambda: settings, spawner=DrainBlockedSpawner())
    task = asyncio.create_task(service.execute(ExecuteRequest(command="fake", owner="draining")))
    async with asyncio.timeout(2):
        await draining.wait()
        assert (await service.status())["queued"] == 0
        await service.cancel("draining")
        result = await task
    assert result["status"] == "cancelled" and result["output_complete"] is False
    assert terminated and not service.is_busy


async def test_append_respects_existing_file_modification_policy(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("before", encoding="utf-8")
    tools, context, settings = registry(Bot())
    settings.workspace.enabled = True
    settings.workspace.root = str(tmp_path)
    policy = settings.groups["10"].policy
    policy.categories["workspace"] = True
    rule = ParameterRule(allowed=[False])
    policy.parameters["workspace__write_file"] = {"overwrite": rule}
    service = WorkspaceService(lambda: settings.workspace)
    for tool in workspace_tools(service):
        tools.add(tool)
    arguments: JsonObject = {"path": "keep.txt", "content": "after", "append": True}
    result = await tools.execute(context, "workspace__write_file", arguments)
    assert result.error_type == "PermissionDenied"
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "before"
    rule.allowed = [True]
    assert (await tools.execute(context, "workspace__write_file", arguments)).status == "ok"
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "beforeafter"


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
