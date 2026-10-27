"""会话各自选择模型、轮内压缩目标与闲时压缩。"""

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest
from test_harness_core import Empty, FakeHub, make_store
from test_provider_context import answer, call_answer
from test_runtime_sessions import message, sessions, tool_response, wake

from qq_agent_harness.configuration import ConfigError, ConfigStore
from qq_agent_harness.context import estimate_tokens
from qq_agent_harness.contracts import JsonObject, ToolDefinition, ToolResult, schema
from qq_agent_harness.providers import ContextLimitError, ModelSession
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.runtime import AgentRuntime
from qq_agent_harness.settings import (
    IdleCompactionSettings,
    ModelChoice,
    ModelEndpoint,
    ProviderSettings,
    SessionModelSettings,
)

KEY = "bot:group:a"
SESSION_URL = "https://api.openai.com/v1/responses"
CHEAP_URL = "https://cheap.example/v1/chat/completions"


def runtime_for(
    store: ConfigStore, client: httpx.AsyncClient, registry: ToolRegistry | None = None
) -> AgentRuntime:
    return AgentRuntime(
        store,
        registry or ToolRegistry(lambda: store.settings),
        FakeHub(),
        session_factory=lambda *args: ModelSession(*args, client=client),
    )


def enable_idle(store: ConfigStore, model: ModelChoice | None = None) -> None:
    store.settings.providers["cheap"] = ProviderSettings(
        protocol="chat_completions", base_url="https://cheap.example/v1"
    )
    store.settings.groups["a"].idle_compaction = IdleCompactionSettings(
        enabled=True, after_minutes=5, min_tokens=0, model=model or ModelChoice()
    )


def entries(runtime: AgentRuntime) -> list[JsonObject]:
    detail = runtime.inspect(KEY, since=None, session_id="")
    assert detail is not None and isinstance(values := detail["entries"], list)
    result: list[JsonObject] = []
    for value in values:
        assert isinstance(value, dict)
        result.append(value)
    return result


def context_text(body: JsonObject) -> str:
    return json.dumps(body.get("input", body.get("messages")), ensure_ascii=False)


async def finish(runtime: AgentRuntime) -> None:
    await asyncio.gather(*list(runtime.workers.values()))


async def test_each_conversation_uses_its_own_provider_and_model(tmp_path):
    store = make_store(tmp_path)
    store.settings.providers["other"] = ProviderSettings(
        protocol="chat_completions", base_url="https://other.example/v1"
    )
    store.settings.groups["b"].model = SessionModelSettings(
        provider="other", name="model-b", reasoning_effort="low"
    )
    store.settings.private.model = SessionModelSettings()
    seen: list[tuple[str, object, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((str(request.url), body["model"], body.get("reasoning_effort")))
        chat = request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json=answer("chat_completions" if chat else "responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = runtime_for(store, client)
        await wake(runtime, message("one", "a", "group", "member"))
        await wake(runtime, message("two", "b", "group", "member"))
        await wake(runtime, message("three"))
        await runtime.close()
    assert seen == [
        (SESSION_URL, "test", None),
        ("https://other.example/v1/chat/completions", "model-b", "low"),
    ]
    # 未选模型的私聊不建立会话，也不发出请求。
    assert runtime.activity[-1]["kind"] == "error" and "bot:private:person" not in runtime.sessions


def test_example_config_loads_with_current_schema(tmp_path):
    # 示例配置随结构调整同步更新；加载即完成字段、引用和文件校验。
    shutil.copytree(Path(__file__).resolve().parent.parent / "config.example", tmp_path / "config")
    ConfigStore(tmp_path / "config" / "harness.toml")


def test_conversation_models_must_reference_existing_providers(tmp_path):
    store = make_store(tmp_path)
    original = store.path.read_bytes()
    data = store.settings.model_dump(mode="json")
    data["groups"]["a"]["idle_compaction"]["model"]["provider"] = "missing"
    with pytest.raises(ConfigError) as error:
        store.save(data, store.last_sha)
    assert [issue["path"] for issue in error.value.issues] == [
        ["groups", "a", "idle_compaction", "model", "provider"]
    ]
    assert store.path.read_bytes() == original


@pytest.mark.parametrize(("target", "kept"), [(50, True), (15, False)])
async def test_compaction_target_controls_how_much_recent_context_stays(target, kept):
    bodies: list[JsonObject] = []
    events: list[JsonObject] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            return httpx.Response(200, json=answer("responses", "旧资料摘要"))
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=call_answer("responses", "recent"))
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(model="test", max_context_tokens=8192, compaction_target_percent=target),
            "system",
            "CURRENT",
            client=client,
        )
        session.on_context_event = events.append
        reply = await session.step([])
        session.result(
            reply.calls[0], ToolResult.success({"value": "RECENT_" + "r" * 1700}), 10_000
        )
        session.add_background("历史" * 2500)
        await session.step([])
    compacted = [event for event in events if event["phase"] == "compacted"]
    assert len(compacted) == 1
    after = compacted[0]["after_tokens"]
    assert isinstance(after, int) and after <= 8192 * target // 100
    assert ("RECENT_" in context_text(bodies[-1])) is kept
    assert "历史历史" not in context_text(bodies[-1])


async def test_idle_compaction_reports_shrink_in_api_calibrated_tokens():
    # 接口用量远低于字节估算时，压缩前后的数字须同一口径，不能把缩小显示成变大。
    def respond(request: httpx.Request) -> httpx.Response:
        if "tools" not in json.loads(request.content):
            return httpx.Response(200, json=answer("chat_completions", "S" * 3000))
        usage = {"input_tokens": 1000, "output_tokens": 25}
        return httpx.Response(200, json=answer("responses", "ok", usage=usage))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(ModelEndpoint(model="test"), "system", "a" * 6000, client=client)
        await session.step([])
        shown = session.context_state["input_tokens"]
        cheap = ModelEndpoint(
            protocol="chat_completions", base_url="https://cheap.example/v1", model="small"
        )
        before, after = await session.compact_idle(cheap, request_limit=1)
    assert before == shown and after < before
    assert session.context_state["input_tokens"] == after


def is_original_source(body: JsonObject) -> bool:
    """第一次摘要的输入是旧资料原文；再次压缩时输入的是上一次的摘要。"""
    return "OLD_" in json.dumps(body, ensure_ascii=False)


async def test_turn_compaction_keeps_recent_group_by_api_measured_size():
    # 接口计量只有本地估算一半：最近调用组按估算超出尾部保留预算，按接口口径仍在预算内，应保留原文。
    bodies: list[JsonObject] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            return httpx.Response(200, json=answer("responses", "旧资料摘要"))
        bodies.append(body)
        usage = {"input_tokens": estimate_tokens(body) // 2, "output_tokens": 0}
        data = call_answer("responses", "recent") if len(bodies) == 4 else answer("responses")
        return httpx.Response(200, json={**data, "usage": usage})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(model="test", max_context_tokens=8192), "system", "CURRENT", client=client
        )
        # 旧资料分四轮放入，每轮都由接口计量校准；第四轮模型发起工具调用。
        for index in range(3):
            session.add_background(f"OLD_{index}" + "史" * 1633)
            await session.step([])
        session.add_background("OLD_3" + "史" * 1633)
        reply = await session.step([])
        session.result(
            reply.calls[0], ToolResult.success({"value": "RECENT_" + "r" * 3550}), 10_000
        )
        await session.step([])
    assert len(bodies) == 5
    context = context_text(bodies[-1])
    assert "RECENT_" in context and "OLD_" not in context


async def test_turn_compaction_reaches_target_and_reports_in_api_measured_size():
    # 接口计量是本地估算的两倍：按估算已达目标的摘要实际仍超过触发点，须再压缩一次；
    # 报告的前后数字与接口计量吻合。
    events: list[JsonObject] = []
    measured: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            text = "S" * 7000 if is_original_source(body) else "短摘要"
            return httpx.Response(200, json=answer("responses", text))
        measured.append(2 * estimate_tokens(body))
        usage = {"input_tokens": measured[-1], "output_tokens": 0}
        return httpx.Response(200, json=answer("responses", "ok", usage=usage))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(model="test", max_context_tokens=8192), "system", "CURRENT", client=client
        )
        session.on_context_event = events.append
        session.add_background("OLD_A" + "历" * 1000)
        await session.step([])
        session.add_background("OLD_B" + "史" * 2000)
        await session.step([])
        await session.step([])
    compacted = [event for event in events if event["phase"] == "compacted"]
    assert len(compacted) == 1 and len(measured) == 3
    assert measured[2] <= 8192 * 50 // 100
    before, after = compacted[0]["before_tokens"], compacted[0]["after_tokens"]
    assert before == measured[1]
    assert isinstance(after, int) and abs(after - measured[2]) * 50 <= measured[2]


async def test_forced_compaction_does_not_trust_calibration_disproved_by_overflow():
    # 接口计量只有估算一半，但模型实际窗口小于配置预算：接口报告超窗后，强制压缩不能再按偏低的
    # 校准放宽，否则压缩后的请求仍会超窗。
    bodies: list[JsonObject] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            text = "S" * 8600 if is_original_source(body) else "短摘要"
            return httpx.Response(200, json=answer("responses", text))
        measured = estimate_tokens(body) // 2
        if measured > 1800:
            error = {"error": {"code": "context_length_exceeded", "message": "too long"}}
            return httpx.Response(400, json=error)
        bodies.append(body)
        usage = {"input_tokens": measured, "output_tokens": 0}
        return httpx.Response(200, json=answer("responses", "ok", usage=usage))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ModelEndpoint(model="test", max_context_tokens=8192), "system", "CURRENT", client=client
        )
        session.add_background("OLD_A" + "历" * 1800)
        await session.step([])
        session.add_background("OLD_B" + "史" * 1270)
        await session.step([])
    assert len(bodies) == 2 and "OLD_" not in context_text(bodies[-1])


async def test_idle_estimate_counts_tools_when_first_turn_exceeds_budget():
    # 首轮超出预算、没有发出请求时，闲置估算仍要计入工具定义，与预算检查同一口径。
    events: list[JsonObject] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as client:
        session = ModelSession(
            ModelEndpoint(model="test", max_context_tokens=4096),
            "system",
            "CURRENT " * 2000,
            client=client,
        )
        session.on_context_event = events.append
        with pytest.raises(ContextLimitError):
            await session.step([{"name": "lookup", "description": "schema " * 200}])
    checked = [event["input_tokens"] for event in events if event["phase"] == "checking"]
    assert session.request_count == 0 and session.idle_pressure() == checked[-1]


async def test_idle_compaction_uses_its_own_model_and_keeps_only_persona_and_knowledge(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / "knowledge.md").write_text("KNOWLEDGE_FACT", encoding="utf-8")
    store.settings.knowledge_files = ["knowledge.md"]
    enable_idle(
        store,
        ModelChoice(
            provider="cheap", name="small", reasoning_effort="low", max_context_tokens=8192
        ),
    )
    requests: list[tuple[str, JsonObject]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        if "tools" not in body:
            return httpx.Response(200, json=answer("chat_completions", "IDLE_SUMMARY"))
        return httpx.Response(200, json=answer("responses", "ASSISTANT_NOTE"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = runtime_for(store, client)
        await wake(runtime, message("first", "a", "group", "member"))
        ended = runtime.sessions[KEY].updated_at
        assert runtime.compact_idle_sessions(now=ended + 299) == []
        assert runtime.compact_idle_sessions(now=ended + 300) == [KEY]
        await finish(runtime)
        url, summary = requests[-1]
        source = context_text(summary)
        assert url == CHEAP_URL and summary["model"] == "small"
        assert summary["reasoning_effort"] == "low" and "tools" not in summary
        assert "INPUT_first" in source and "ASSISTANT_NOTE" in source
        assert "KNOWLEDGE_FACT" not in source and "测试人格" not in source
        kept = entries(runtime)
        assert [entry["kind"] for entry in kept] == ["system", "knowledge", "summary"]
        assert kept[2]["origin"] == "idle" and kept[2]["model"] == "small"
        assert "IDLE_SUMMARY" in str(kept[2]["text"])
        done = runtime.activity[-1]
        assert done["kind"] == "compaction" and done["status"] == "completed"
        assert "small" in str(done["message"])
        # 同一段闲置只压缩一次；新的一轮结束后重新计时。
        assert runtime.compact_idle_sessions(now=ended + 3600) == []
        await wake(runtime, message("second", "a", "group", "member"))
        url, body = requests[-1]
        context = context_text(body)
        assert url == SESSION_URL
        assert "IDLE_SUMMARY" in context and "KNOWLEDGE_FACT" in context
        assert "INPUT_second" in context and "INPUT_first" not in context
        ended = runtime.sessions[KEY].updated_at
        assert runtime.compact_idle_sessions(now=ended + 300) == [KEY]
        await finish(runtime)
        await runtime.close()


async def test_wake_during_idle_compaction_waits_for_the_summary(tmp_path):
    store = make_store(tmp_path)
    enable_idle(store, ModelChoice(provider="cheap", name="small"))
    release = asyncio.Event()
    order: list[str] = []
    turns: list[JsonObject] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            order.append("summary")
            await release.wait()
            return httpx.Response(200, json=answer("chat_completions", "IDLE_SUMMARY"))
        order.append("turn")
        turns.append(body)
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = runtime_for(store, client)
        await wake(runtime, message("first", "a", "group", "member"))
        assert runtime.compact_idle_sessions(now=runtime.sessions[KEY].updated_at + 300) == [KEY]
        async with asyncio.timeout(5):
            while "summary" not in order:
                await asyncio.sleep(0)
        await runtime.receive(message("second", "a", "group", "member"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert order == ["turn", "summary"]
        waiting = sessions(runtime)[0]
        assert waiting["state"] == "compacting" and waiting["pending"] == 1
        release.set()
        await finish(runtime)
        await runtime.close()
    assert order == ["turn", "summary", "turn"]
    context = context_text(turns[-1])
    assert "IDLE_SUMMARY" in context and "INPUT_second" in context
    assert "INPUT_first" not in context


async def test_failed_idle_compaction_keeps_context_and_is_not_retried(tmp_path):
    store = make_store(tmp_path)
    enable_idle(store, ModelChoice(provider="cheap", name="small"))
    summaries = 0
    turns: list[JsonObject] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal summaries
        body = json.loads(request.content)
        if "tools" not in body:
            summaries += 1
            return httpx.Response(500, json={"error": {"message": "UPSTREAM_DETAIL"}})
        turns.append(body)
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = runtime_for(store, client)
        await wake(runtime, message("first", "a", "group", "member"))
        ended = runtime.sessions[KEY].updated_at
        assert runtime.compact_idle_sessions(now=ended + 300) == [KEY]
        await finish(runtime)
        failure = runtime.activity[-1]
        assert failure["kind"] == "compaction" and failure["status"] == "error"
        assert "UPSTREAM_DETAIL" not in str(failure["message"])
        assert "summary" not in [entry["kind"] for entry in entries(runtime)]
        assert runtime.compact_idle_sessions(now=ended + 3600) == []
        await wake(runtime, message("second", "a", "group", "member"))
        await runtime.close()
    assert summaries == 1
    assert "INPUT_first" in context_text(turns[-1])


async def test_stopping_idle_compaction_keeps_original_context(tmp_path):
    store = make_store(tmp_path)
    enable_idle(store, ModelChoice(provider="cheap", name="small"))
    requested = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        if "tools" not in json.loads(request.content):
            requested.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = runtime_for(store, client)
        await wake(runtime, message("first", "a", "group", "member"))
        assert runtime.compact_idle_sessions(now=runtime.sessions[KEY].updated_at + 300) == [KEY]
        async with asyncio.timeout(5):
            await requested.wait()
        assert await runtime.cancel(KEY)
        stopped = runtime.activity[-1]
        assert stopped["kind"] == "compaction" and stopped["status"] == "cancelled"
        kinds = [entry["kind"] for entry in entries(runtime)]
        assert "wake" in kinds and "summary" not in kinds
        assert sessions(runtime)[0]["state"] == "idle"
        await runtime.close()


async def test_idle_compaction_falls_back_to_session_model_above_threshold(tmp_path):
    store = make_store(tmp_path)
    store.settings.groups["a"].model.reasoning_effort = "high"
    enable_idle(store)
    idle = store.settings.groups["a"].idle_compaction
    summaries: list[tuple[str, JsonObject]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            summaries.append((str(request.url), body))
            return httpx.Response(200, json=answer("responses", "IDLE_SUMMARY"))
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = runtime_for(store, client)
        await wake(runtime, message("first", "a", "group", "member"))
        ended = runtime.sessions[KEY].updated_at
        idle.enabled = False
        assert runtime.compact_idle_sessions(now=ended + 300) == []
        idle.enabled = True
        idle.min_tokens = 1_000_000
        assert runtime.compact_idle_sessions(now=ended + 300) == []
        idle.min_tokens = 0
        assert runtime.compact_idle_sessions(now=ended + 300) == [KEY]
        await finish(runtime)
        await runtime.close()
    assert len(summaries) == 1
    url, body = summaries[0]
    # 没有单独选择压缩模型时沿用会话模型，摘要请求与轮内压缩一样不带思考等级。
    assert url == SESSION_URL and body["model"] == "test" and "reasoning" not in body


async def test_knowledge_folded_by_turn_compaction_returns_after_idle_compaction(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / "knowledge.md").write_text("KNOWLEDGE_FACT " * 100, encoding="utf-8")
    store.settings.knowledge_files = ["knowledge.md"]
    store.settings.groups["a"].model.max_context_tokens = 8192
    enable_idle(store, ModelChoice(provider="cheap", name="small"))
    registry = ToolRegistry(lambda: store.settings)

    async def large(_context, _arguments):
        return ToolResult.success({"facts": "fact " * 2800})

    registry.add(
        ToolDefinition(
            "qq__send_message",
            "读取大量资料",
            "messages.send",
            schema(Empty),
            large,
            model=Empty,
        )
    )
    turns: list[JsonObject] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" in body:
            turns.append(body)
            payload = tool_response("responses", "big") if len(turns) == 1 else answer("responses")
            return httpx.Response(200, json=payload)
        chat = request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json=answer("chat_completions", "IDLE_SUMMARY")
            if chat
            else answer("responses", "TURN_SUMMARY"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        runtime = runtime_for(store, client, registry)
        await wake(runtime, message("first", "a", "group", "member"))
        assert "KNOWLEDGE_FACT" in context_text(turns[0])
        assert "KNOWLEDGE_FACT" not in context_text(turns[1])
        turn_summary = [entry for entry in entries(runtime) if entry["kind"] == "summary"]
        assert [(entry["origin"], entry["model"]) for entry in turn_summary] == [("turn", "test")]
        assert runtime.compact_idle_sessions(now=runtime.sessions[KEY].updated_at + 300) == [KEY]
        await finish(runtime)
        await wake(runtime, message("second", "a", "group", "member"))
        await runtime.close()
    context = context_text(turns[-1])
    assert "KNOWLEDGE_FACT" in context and "IDLE_SUMMARY" in context
