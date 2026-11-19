"""Agent Harness 升级回归：工作区治理、流式协议、零 LLM 预裁剪、并发只读、打断、死循环、技能/子代理/定时器与控制台鉴权。"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import tomlkit
from fastapi.testclient import TestClient
from test_harness_core import Empty, FakeHub, make_store
from test_napcat import Bot
from test_napcat import registry as napcat_registry
from test_provider_context import answer

from qq_agent_harness.contracts import (
    ConversationScope,
    GeneratedMedia,
    JsonObject,
    Message,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    json_binding,
    model_binding,
    remember_generated,
    schema,
)
from qq_agent_harness.napcat.media import MediaReader
from qq_agent_harness.prompts import SILENT_RESPONSE, compose_prompt
from qq_agent_harness.providers import MediaInput, ModelSession
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.runtime import AgentRuntime
from qq_agent_harness.session_view import project
from qq_agent_harness.settings import ModelEndpoint, WorkspaceSettings
from qq_agent_harness.skills import build_tools as skill_tools
from qq_agent_harness.subagent import SubagentManager
from qq_agent_harness.subagent import build_tools as subagent_tools
from qq_agent_harness.timers import TimerManager
from qq_agent_harness.timers import build_tools as timer_tools
from qq_agent_harness.web import create_app
from qq_agent_harness.workspace import ExecuteRequest, WorkspaceService
from qq_agent_harness.workspace import build_tools as workspace_tools
from qq_agent_harness.workspace.executor import RunningProcess

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="
)


async def test_workspace_exec_tmp_head_tail_zero_log_and_concurrent_owners(tmp_path: Path) -> None:
    # 1. 超时且无输出时不残留 0B 日志，且每次执行的 .tmp/exec-* 自动创建并清理
    seen_envs: list[dict[str, str]] = []

    class SilentTimeoutProc:
        def output(self) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                if False:
                    yield b""
                await asyncio.Event().wait()

            return _gen()

        async def wait(self) -> int:
            await asyncio.Event().wait()
            return 0

        async def terminate(self) -> None:
            return None

    class SilentSpawner:
        async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
            seen_envs.append(env)
            assert Path(env["TMPDIR"]).is_dir()
            return SilentTimeoutProc()

    silent_ws = tmp_path / "ws1"
    silent_cfg = WorkspaceSettings(
        enabled=True, root=str(silent_ws), command_timeout=0.02, output_limit=1024
    )
    silent_service = WorkspaceService(lambda: silent_cfg, spawner=SilentSpawner())
    timeout_res = await silent_service.execute(ExecuteRequest(command="sleep 99", owner="g1"))
    assert timeout_res["status"] == "timeout"
    assert timeout_res["output_path"] is None
    assert list((silent_ws / ".outputs").glob("*.log")) == []
    assert list((silent_ws / ".tmp").glob("exec-*")) == []
    assert seen_envs and seen_envs[0]["TMPDIR"].startswith(str((silent_ws / ".tmp").resolve()))

    # 2. 超限多字节 UTF-8 输出保留头尾且不破坏字符边界
    class Utf8OverflowProc:
        def output(self) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                yield ("头部开始_" + "中" * 400 + "_尾部结束").encode()

            return _gen()

        async def wait(self) -> int:
            return 0

        async def terminate(self) -> None:
            return None

    class Utf8Spawner:
        async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
            return Utf8OverflowProc()

    utf8_cfg = WorkspaceSettings(
        enabled=True, root=str(silent_ws), command_timeout=5, output_limit=1024
    )
    utf8_service = WorkspaceService(lambda: utf8_cfg, spawner=Utf8Spawner())
    utf8_res = await utf8_service.execute(ExecuteRequest(command="emit", owner="g1"))
    assert utf8_res["status"] == "ok" and utf8_res["truncated"] is True
    output_text = str(utf8_res["output"])
    assert output_text.startswith("头部开始_")
    assert output_text.endswith("_尾部结束")
    assert "中间省略" in output_text
    assert "\ufffd" not in output_text
    assert len(output_text.encode()) <= 1024
    assert isinstance(utf8_res["output_path"], str)

    # 3. 双并发执行互不干扰：取消 owner-a 不中断 owner-b
    both_started = asyncio.Event()
    finish_b = asyncio.Event()

    class DualProc:
        def __init__(self, cmd: str) -> None:
            self.cmd = cmd
            self.stopped = asyncio.Event()

        def output(self) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                if self.cmd == "a":
                    yield b"from-a"
                    await self.stopped.wait()
                else:
                    await finish_b.wait()
                    yield b"from-b-ok"

            return _gen()

        async def wait(self) -> int:
            if self.cmd == "a":
                await self.stopped.wait()
                return -9
            await finish_b.wait()
            return 0

        async def terminate(self) -> None:
            self.stopped.set()

    class DualSpawner:
        def __init__(self) -> None:
            self.started_count = 0

        async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
            self.started_count += 1
            if self.started_count >= 2:
                both_started.set()
            return DualProc(command)

    dual_cfg = WorkspaceSettings(
        enabled=True, root=str(tmp_path / "ws2"), command_timeout=5, concurrent_executions=2
    )
    dual_service = WorkspaceService(lambda: dual_cfg, spawner=DualSpawner())
    task_a = asyncio.create_task(dual_service.execute(ExecuteRequest(command="a", owner="owner-a")))
    task_b = asyncio.create_task(dual_service.execute(ExecuteRequest(command="b", owner="owner-b")))
    async with asyncio.timeout(2):
        await both_started.wait()
        status_mid = await dual_service.status()
        assert status_mid["active_count"] == 2
        cancel_res = await dual_service.cancel("owner-a")
        assert cancel_res["cancelled"] is True
        res_a = await task_a
        assert res_a["status"] == "cancelled"
        finish_b.set()
        res_b = await task_b
        assert res_b["status"] == "ok" and res_b["output"] == "from-b-ok"


async def test_workspace_edit_search_delete_and_temp_cleanup(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    store = make_store(cfg_dir)
    store.settings.workspace.enabled = True
    store.settings.workspace.root = str(work_dir)
    store.settings.groups["a"].policy.categories["workspace"] = True
    service = WorkspaceService(lambda: store.settings.workspace, config_root=cfg_dir)
    registry = ToolRegistry(lambda: store.settings)
    for tool in workspace_tools(service):
        registry.add(tool)
    scope = ConversationScope(bot_id="b", kind="group", peer_id="a")
    ctx = ToolContext(scope=scope, turn_id="t1", napcat=Bot())

    (work_dir / "notes").mkdir(parents=True, exist_ok=True)
    (work_dir / "notes/todo.py").write_text("x = 1\ny = 1\nz = 2\n", encoding="utf-8")
    (work_dir / ".cache/uv").mkdir(parents=True, exist_ok=True)
    (work_dir / ".cache/uv/pkg.txt").write_text("x = 1 hidden cache\n", encoding="utf-8")
    (work_dir / ".scratch").mkdir(parents=True, exist_ok=True)
    (work_dir / ".scratch/frame.png").write_bytes(PNG_BYTES)
    (work_dir / ".outputs").mkdir(parents=True, exist_ok=True)
    (work_dir / ".outputs/old.log").write_text("log", encoding="utf-8")

    dup_err = await registry.execute(
        ctx,
        "workspace__edit_file",
        {"path": "notes/todo.py", "old_string": "= 1", "new_string": "= 10"},
    )
    assert dup_err.status == "error" and dup_err.error_type == "InvalidOperation"

    ok_edit = await registry.execute(
        ctx,
        "workspace__edit_file",
        {
            "path": "notes/todo.py",
            "old_string": "= 1",
            "new_string": "= 10",
            "replace_all": True,
        },
    )
    assert ok_edit.status == "ok" and isinstance(ok_edit.data, dict)
    assert ok_edit.data["replacements"] == 2
    assert "x = 10\ny = 10\n" in (work_dir / "notes/todo.py").read_text(encoding="utf-8")

    found = await registry.execute(ctx, "workspace__search_files", {"query": "= 10"})
    assert found.status == "ok" and isinstance(found.data, dict)
    assert isinstance(matches := found.data["matches"], list) and len(matches) == 2

    hidden_skip = await registry.execute(ctx, "workspace__search_files", {"query": "hidden cache"})
    assert hidden_skip.status == "ok" and isinstance(hidden_skip.data, dict)
    assert hidden_skip.data["matches"] == []

    (work_dir / "drafts/sub").mkdir(parents=True, exist_ok=True)
    (work_dir / "drafts/sub/a.txt").write_text("hi", encoding="utf-8")
    del_fail = await registry.execute(ctx, "workspace__delete_path", {"path": "drafts"})
    assert del_fail.status == "error" and del_fail.error_type == "InvalidOperation"
    del_ok = await registry.execute(
        ctx, "workspace__delete_path", {"path": "drafts", "recursive": True}
    )
    assert del_ok.status == "ok" and not (work_dir / "drafts").exists()

    await service.cleanup("temp", [], True)
    assert (work_dir / "notes/todo.py").exists()
    assert (work_dir / ".cache/uv/pkg.txt").exists()
    assert list((work_dir / ".scratch").iterdir()) == []
    assert list((work_dir / ".outputs").iterdir()) == []


async def test_save_media_defaults_to_scratch_and_supports_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public_ip(_: str) -> str:
        return "8.8.8.8"

    def serve(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})

    saved_calls: list[tuple[str, bytes, bool]] = []

    async def fake_write(path: str, data: bytes, *, overwrite: bool = False) -> None:
        saved_calls.append((path, data, overwrite))

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reader = MediaReader(client=http_client, resolver=public_ip)
        monkeypatch.setattr("qq_agent_harness.napcat.tools.MediaReader", lambda _: reader)
        bot = Bot()
        tools, context, settings = napcat_registry(bot)
        settings.workspace.enabled = True
        settings.groups["10"].policy.categories["workspace"] = True
        tools.add(
            ToolDefinition(
                "workspace__write_file",
                "写入文件桩",
                "workspace",
                model_binding(Empty, lambda _c, _a: asyncio.sleep(0, ToolResult.success())),
            )
        )
        context.write_workspace = fake_write

        auto_res = await tools.execute(
            context, "qq__save_media_to_workspace", {"user_id": "20", "indexes": [0]}
        )
        assert auto_res.status == "ok" and isinstance(auto_res.data, dict)
        auto_path = str(auto_res.data["path"])
        assert auto_path.startswith(".scratch/media_") and auto_path.endswith(".png")
        assert saved_calls[-1][2] is False

        explicit_res = await tools.execute(
            context,
            "qq__save_media_to_workspace",
            {
                "user_id": "20",
                "indexes": [0],
                "destination": "frames/out.png",
                "overwrite": True,
            },
        )
        assert explicit_res.status == "ok"
        assert saved_calls[-1] == ("frames/out.png", PNG_BYTES, True)


class _ChunkedByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay: float = 0.0, hang_after: bool = False):
        self._chunks = chunks
        self._delay = delay
        self._hang_after = hang_after

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield chunk
        if self._hang_after:
            await asyncio.sleep(10)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_sse_streaming_utf8_tool_deltas_idle_timeout_and_fallback(
    protocol: Literal["chat_completions", "responses"],
) -> None:
    utf8_text = "你好世界"
    utf8_bytes = utf8_text.encode("utf-8")
    attempt = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        body = json.loads(request.content)
        assert body["stream"] is True

        if attempt == 1:
            if protocol == "chat_completions":
                assert "stream_options" in body
                return httpx.Response(
                    400,
                    json={"error": {"message": "Unknown parameter: stream_options"}},
                )
            incomplete = [
                b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_ChunkedByteStream(incomplete),
            )

        if attempt == 2:
            if protocol == "chat_completions":
                assert "stream_options" not in body
            first_event = (
                b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'
                if protocol == "chat_completions"
                else b'data: {"type":"response.created"}\n\n'
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_ChunkedByteStream([first_event], hang_after=True),
            )

        if protocol == "chat_completions":
            raw_line = (
                b'data: {"choices":[{"delta":{"content":"'
                + utf8_bytes
                + b'","tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"qq__ping","arguments":"{\\"a\\":"}}]}}]}\n\n'
            )
            split_at = raw_line.index(utf8_bytes) + 2
            ev_tail = (
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]},"finish_reason":"tool_calls"}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":200,"completion_tokens":30,"total_tokens":230,"prompt_tokens_details":{"cached_tokens":150},"completion_tokens_details":{"reasoning_tokens":12}}}\n\n'
                b"data: [DONE]\n\n"
            )
            chunks = [raw_line[:split_at], raw_line[split_at:], ev_tail]
        else:
            completed_obj = {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": utf8_text}],
                        },
                        {
                            "type": "function_call",
                            "call_id": "c1",
                            "name": "qq__ping",
                            "arguments": '{"a":1}',
                        },
                    ],
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 30,
                        "total_tokens": 230,
                        "input_tokens_details": {"cached_tokens": 150},
                        "output_tokens_details": {"reasoning_tokens": 12},
                    },
                },
            }
            raw_sse = (
                b"data: " + json.dumps(completed_obj, ensure_ascii=False).encode("utf-8") + b"\n\n"
            )
            split_at = raw_sse.index(utf8_bytes) + 2
            chunks = [raw_sse[:split_at], raw_sse[split_at:]]

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            stream=_ChunkedByteStream(chunks),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        endpoint = ModelEndpoint(
            base_url=f"https://stream-test-{protocol}.invalid/v1",
            protocol=protocol,
            model="test-stream",
            timeout=5.0,
            idle_timeout=0.08,
            stream=True,
            max_retries=3,
        )
        session = ModelSession(endpoint, "sys", "hi", client=client)
        reply = await session.step([])
        assert reply.text == utf8_text
        assert len(reply.calls) == 1
        assert reply.calls[0].name == "qq__ping"
        assert reply.calls[0].arguments == {"a": 1}
        state = session.context_state
        assert state["cached_tokens"] == 150
        assert state["reasoning_tokens"] == 12
        assert attempt == 3


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_two_stage_pre_compaction_prunes_large_results_and_offloads_old_images_without_llm_summary(
    protocol: Literal["chat_completions", "responses"],
) -> None:
    step_no = 0
    seen_bodies: list[JsonObject] = []

    def full_flow(request: httpx.Request) -> httpx.Response:
        nonlocal step_no
        step_no += 1
        body = json.loads(request.content)
        seen_bodies.append(body)
        if step_no == 1:
            if protocol == "chat_completions":
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "c-big",
                                            "type": "function",
                                            "function": {
                                                "name": "qq__read_media",
                                                "arguments": "{}",
                                            },
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "c-big",
                            "name": "qq__read_media",
                            "arguments": "{}",
                        }
                    ],
                },
            )
        return httpx.Response(200, json=answer(protocol, SILENT_RESPONSE))

    async with httpx.AsyncClient(transport=httpx.MockTransport(full_flow)) as client:
        endpoint = ModelEndpoint(
            protocol=protocol,
            model="test-prune",
            max_context_tokens=26_000,
            modalities=["image"],
            stream=False,
        )
        session = ModelSession(endpoint, "system", "round-1", client=client)
        r1 = await session.step([])
        assert len(r1.calls) == 1
        huge_text = "HEAD_MARK_" + ("X" * 28_000) + "_TAIL_MARK"
        session.result(r1.calls[0], ToolResult.success({"content": huge_text}))
        # 注入大图使得第 1 轮收尾时未超 80%，但进入第 2 轮后同时触发一级裁剪与二级卸载
        big_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 18_000).decode("ascii")
        assert session.add_media([MediaInput("image", "image/png", big_b64)]) == []
        await session.step([])
        session.end_turn("completed")

        session.begin_turn("round-2-" + ("Y" * 28_000))
        await session.step([])
        session.end_turn("completed")

        state = session.context_state
        assert isinstance(prunes := state["prunes"], int) and prunes >= 1
        # 全程零 LLM 摘要调用（1 次工具步 + 1 次第 1 轮收尾 + 1 次第 2 轮请求 = 3 次请求，无额外摘要请求）
        assert step_no == 3
        assert state["compactions"] == 0
        serialized_last = json.dumps(seen_bodies[-1], ensure_ascii=False)
        assert "HEAD_MARK_" in serialized_last
        assert "_TAIL_MARK" in serialized_last
        assert "X" * 10_000 not in serialized_last
        assert "已从上下文卸载" in serialized_last


async def test_parallel_read_tools_mid_turn_steering_and_loop_guard(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    registry = ToolRegistry(lambda: store.settings)

    entered_a = asyncio.Event()
    entered_b = asyncio.Event()

    async def read_a(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        entered_a.set()
        await asyncio.wait_for(entered_b.wait(), timeout=1.0)
        return ToolResult.success({"from": "a"})

    async def read_b(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        entered_b.set()
        await asyncio.wait_for(entered_a.wait(), timeout=1.0)
        return ToolResult.success({"from": "b"})

    fail_calls = 0

    async def always_fail(_ctx: ToolContext, _args: JsonObject) -> ToolResult:
        nonlocal fail_calls
        fail_calls += 1
        return ToolResult.failure("固定故障", "FixedError")

    registry.add(
        ToolDefinition(
            "qq__parallel_a",
            "并发只读 A",
            "messages",
            json_binding(schema(Empty), read_a),
            concurrency_safe=True,
        )
    )
    registry.add(
        ToolDefinition(
            "qq__parallel_b",
            "并发只读 B",
            "messages",
            json_binding(schema(Empty), read_b),
            concurrency_safe=True,
        )
    )
    registry.add(
        ToolDefinition(
            "qq__broken_op",
            "重复失败工具",
            "messages",
            json_binding(schema(Empty), always_fail),
        )
    )

    scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
    runtime_ref: list[AgentRuntime] = []
    step_idx = 0
    seen_payloads: list[str] = []

    def transport_fn(request: httpx.Request) -> httpx.Response:
        nonlocal step_idx
        step_idx += 1
        seen_payloads.append(request.content.decode("utf-8"))
        if step_idx == 1:
            rt = runtime_ref[0]
            rt.pending.setdefault(scope.key, []).append(
                Message(
                    scope=scope,
                    message_id="mid-99",
                    sender_id="u2",
                    segments=[{"type": "text", "data": {"text": "MID_TURN_STEERING_MSG"}}],
                )
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "pa",
                                        "type": "function",
                                        "function": {"name": "qq__parallel_a", "arguments": "{}"},
                                    },
                                    {
                                        "id": "pb",
                                        "type": "function",
                                        "function": {"name": "qq__parallel_b", "arguments": "{}"},
                                    },
                                ],
                            },
                        }
                    ]
                },
            )
        if 2 <= step_idx <= 9:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": f"fail-{step_idx}",
                                        "type": "function",
                                        "function": {"name": "qq__broken_op", "arguments": "{}"},
                                    }
                                ],
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json=answer("chat_completions", SILENT_RESPONSE))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_fn)) as client:
        runtime = AgentRuntime(
            store,
            registry,
            FakeHub(),
            session_factory=lambda settings, system, conv: ModelSession(
                settings, system, conv, client=client
            ),
        )
        runtime_ref.append(runtime)
        await runtime.receive(
            Message(
                scope=scope,
                message_id="m1",
                sender_id="u1",
                segments=[
                    {"type": "at", "data": {"qq": "bot"}},
                    {"type": "text", "data": {"text": "START"}},
                ],
            )
        )
        await asyncio.gather(*list(runtime.workers.values()))

    assert "MID_TURN_STEERING_MSG" in seen_payloads[1]
    assert fail_calls == 7
    assert "RepeatedToolCallBlocked" in seen_payloads[-1]


class _MakeMediaArg(Model):
    tag: str


async def test_skills_subagent_timer_and_console_auth(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.settings.providers["test"].protocol = "chat_completions"
    store.settings.providers["test"].stream = False
    store.settings.image.enabled = True
    store.settings.image.model = "mock-img"
    store.settings.server.auth_token = "console-secret-token-12345"
    store.settings.groups["a"].policy.categories["skills"] = True
    store.settings.groups["a"].policy.categories["subagent"] = True
    store.settings.groups["a"].policy.categories["timer"] = True
    store.path.write_text(
        tomlkit.dumps(store.settings.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    store.reload()

    store.save_file(
        "skills/gif-check.md",
        "---\nname: gif-check\ndescription: GIF 抽帧检查规范\n---\n请先将临时帧放入 /tmp。\n",
        expected_sha=None,
    )

    registry = ToolRegistry(lambda: store.settings)
    for t in skill_tools(lambda: store):
        registry.add(t)

    async def make_media(ctx: ToolContext, args: _MakeMediaArg) -> ToolResult:
        gid = f"gen-{args.tag}"
        remember_generated(ctx.generated_media, GeneratedMedia(gid, PNG_BYTES, "image/png"))
        return ToolResult.success({"generated_id": gid})

    async def send_msg(_ctx: ToolContext, _args: Empty) -> ToolResult:
        return ToolResult.success({"sent": True})

    registry.add(
        ToolDefinition(
            "image__mock_gen",
            "生成测试图",
            "messages",
            model_binding(_MakeMediaArg, make_media),
        )
    )
    registry.add(
        ToolDefinition(
            "qq__send_message",
            "发送消息",
            "messages",
            model_binding(Empty, send_msg),
        )
    )

    woke_notes: list[str] = []

    async def on_timer(scope: ConversationScope, note: str, timer_id: str) -> None:
        woke_notes.append(f"{scope.key}:{note}:{timer_id}")

    timers = TimerManager(on_fire=on_timer)
    for t in timer_tools(timers):
        registry.add(t)

    sub_step = 0
    sub_tool_names: list[list[str]] = []

    def sub_transport(request: httpx.Request) -> httpx.Response:
        nonlocal sub_step
        sub_step += 1
        body = json.loads(request.content)
        tools_payload = body.get("tools", [])
        sub_tool_names.append(
            [
                fn["name"]
                for item in tools_payload
                if isinstance(item, dict) and isinstance(fn := item.get("function"), dict)
            ]
        )
        if sub_step == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "sc1",
                                        "type": "function",
                                        "function": {
                                            "name": "image__mock_gen",
                                            "arguments": '{"tag":"chart"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json=answer("chat_completions", "子代理已完成图表生成"))

    sub_wakes: list[tuple[str, str]] = []

    async def on_sub_wake(
        _scope: ConversationScope,
        _parent_sid: str,
        _event_id: str,
        event_type: str,
        _title: str,
        text: str,
    ) -> bool:
        sub_wakes.append((event_type, text))
        return True

    async with httpx.AsyncClient(transport=httpx.MockTransport(sub_transport)) as client:
        sub_manager = SubagentManager(
            lambda: store,
            registry,
            session_factory=lambda settings, system, conv: ModelSession(
                settings, system, conv, client=client
            ),
            on_wake=on_sub_wake,
        )
        for t in subagent_tools(sub_manager):
            registry.add(t)

        scope = ConversationScope(bot_id="bot", kind="group", peer_id="a")
        ctx = ToolContext(scope=scope, turn_id="t-main", napcat=Bot())

        # 1. Skill 按需加载
        loaded = await registry.execute(ctx, "skill__load", {"name": "gif-check"})
        assert loaded.status == "ok" and isinstance(loaded.data, dict)
        assert "放入 /tmp" in str(loaded.data["instructions"])

        # 2. Subagent 异步隔离执行：剥离 qq__send_message / timer__* / subagent__*，完成后唤醒并回传 generated_media
        spawn_res = await registry.execute(
            ctx,
            "subagent__spawn",
            {"title": "生成图表", "task": "生成图表并汇报"},
        )
        assert spawn_res.status == "ok" and isinstance(spawn_res.data, dict)
        sub_id = str(spawn_res.data["subagent_id"])
        assert spawn_res.data["state"] == "running"
        task_handle = sub_manager.records[sub_id].task_handle
        if task_handle is not None:
            await task_handle
        inspected = await registry.execute(ctx, "subagent__inspect", {"subagent_id": sub_id})
        assert inspected.status == "ok" and isinstance(inspected.data, dict)
        assert inspected.data["state"] == "completed"
        assert inspected.data["report_chunk"] == "子代理已完成图表生成"
        assert inspected.data["generated_ids"] == ["gen-chart"]
        assert "gen-chart" in ctx.generated_media
        assert sub_wakes and sub_wakes[-1][0] == "completed"
        assert "子代理已完成图表生成" in sub_wakes[-1][1]
        exposed = sub_tool_names[0]
        assert "image__mock_gen" in exposed
        assert "qq__send_message" not in exposed
        assert "subagent__spawn" not in exposed
        assert "timer__schedule" not in exposed
        await sub_manager.close()

        # 3. Timer 工具注册/列举/取消 + 极短延迟到期触发验证
        sched = await registry.execute(
            ctx,
            "timer__schedule",
            {"delay_seconds": 10.0, "note": "稍后检查任务"},
        )
        assert sched.status == "ok" and isinstance(sched.data, dict)
        tid = str(sched.data["timer_id"])
        listed = await registry.execute(ctx, "timer__list", {})
        assert listed.status == "ok" and isinstance(listed.data, dict)
        assert isinstance(t_items := listed.data["timers"], list) and len(t_items) == 1
        cancelled = await registry.execute(ctx, "timer__cancel", {"timer_id": tid})
        assert cancelled.status == "ok"

        timers.schedule(scope, 0.02, "极速提醒")
        await asyncio.sleep(0.06)
        assert len(woke_notes) == 1 and "极速提醒" in woke_notes[0]
        await timers.close()

    # 4. WebUI 控制台可选鉴权：/api/health 免鉴权，/api/config 未登录 401，登录后种 HttpOnly Cookie 放行
    app = create_app(store.path, connect_mcp=False)
    with TestClient(app) as web_client:
        assert web_client.get("/api/health").status_code == 200
        status_before = web_client.get("/api/auth/status").json()
        assert status_before == {"protected": True, "authenticated": False}
        unauth = web_client.get("/api/config")
        assert unauth.status_code == 401 and unauth.json()["auth_required"] is True

        bad_login = web_client.post("/api/auth/login", json={"token": "wrong"})
        assert bad_login.status_code == 401

        ok_login = web_client.post("/api/auth/login", json={"token": "console-secret-token-12345"})
        assert ok_login.status_code == 200
        assert web_client.get("/api/auth/status").json() == {
            "protected": True,
            "authenticated": True,
        }
        assert web_client.get("/api/config").status_code == 200

        web_client.post("/api/auth/logout")
        assert web_client.get("/api/config").status_code == 401
        assert (
            web_client.get(
                "/api/config",
                headers={"Authorization": "Bearer console-secret-token-12345"},
            ).status_code
            == 200
        )


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_compaction_first_person_memory_prompt_and_envelope_projection(
    protocol: Literal["chat_completions", "responses"],
) -> None:
    summary_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        serialized = json.dumps(body, ensure_ascii=False)
        if "待整理为第一人称前情记忆的本会话记录" in serialized:
            summary_requests.append(serialized)
            return httpx.Response(
                200,
                json=answer(
                    protocol,
                    "<session_memory>\n权限狗（2172959822）晒出满格额度图，我顺着绿条夸他粮仓爆满。\n</session_memory>",
                ),
            )
        return httpx.Response(200, json=answer(protocol, "闲聊内容_" * 300))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        endpoint = ModelEndpoint(
            protocol=protocol,
            model="test-compact",
            max_context_tokens=8192,
            stream=False,
        )
        session = ModelSession(
            endpoint,
            compose_prompt("你是耄耋，说话干脆利落。"),
            "唤醒消息_" * 200,
            client=client,
        )
        await session.step([])
        session.end_turn("completed")
        await session.compact_idle(endpoint)

    assert len(summary_requests) == 1
    assert "第一人称前情记忆笔记" in summary_requests[0]
    assert "你是耄耋" not in summary_requests[0]

    raw_summary_item = str(session.items[1]["content"])
    assert raw_summary_item.count("<session_memory>") == 1
    assert raw_summary_item.count("</session_memory>") == 1
    assert "自身前情记忆" in raw_summary_item

    projected = project(session)
    assert projected[1]["kind"] == "summary"
    assert projected[1]["text"] == "权限狗（2172959822）晒出满格额度图，我顺着绿条夸他粮仓爆满。"
