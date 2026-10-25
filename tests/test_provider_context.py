"""上下文压缩的协议闭合、失败原子性与请求边界。"""

import asyncio
import copy
import json

import httpx
import pytest

from qq_agent_harness.context import estimate_tokens
from qq_agent_harness.contracts import ToolResult
from qq_agent_harness.providers import (
    CompactionError,
    ContextLimitError,
    ContextWindowExceeded,
    MediaInput,
    ModelRequestLimit,
    ModelSession,
    ProviderError,
)
from qq_agent_harness.settings import ProviderSettings


def answer(protocol, text="完成", **extra):
    if protocol == "chat_completions":
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"role": "assistant", "content": text}}
            ],
            **extra,
        }
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        **extra,
    }


def call_answer(protocol, identifier, *, reasoning="private-reasoning"):
    if protocol == "chat_completions":
        return {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": reasoning,
                        "reasoning_details": [{"text": reasoning}],
                        "tool_calls": [
                            {
                                "id": identifier,
                                "type": "function",
                                "function": {"name": "lookup", "arguments": "{}"},
                            }
                        ],
                    },
                }
            ],
        }
    return {
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "id": "r-" + identifier,
                "encrypted_content": reasoning,
                "summary": [],
            },
            {"type": "function_call", "call_id": identifier, "name": "lookup", "arguments": "{}"},
        ],
    }


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_initial_background_is_chunked_before_first_normal_request(protocol):
    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert estimate_tokens(body) < 4096 * 80 // 100
        if "tools" not in body:
            assert "reasoning_effort" not in body and "reasoning" not in body
            return httpx.Response(200, json=answer(protocol, "已知群聊背景及文件 file-12。"))
        return httpx.Response(200, json=answer(protocol))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(
                protocol=protocol, model="test", max_context_tokens=4096, reasoning_effort="high"
            ),
            "固定人格",
            "当前消息 CURRENT",
            client=client,
        )
        session.add_background("旧群聊内容" * 3000)
        session.action_state = {"send_attempts": 1, "sent_receipts": [{"message_id": "sent-1"}]}
        await session.step([])
        assert session.context_state["compactions"] == 1
        assert session.request_count == len(bodies)
    assert len(bodies) > 3
    field = "messages" if protocol == "chat_completions" else "input"
    assert "固定人格" in json.dumps(bodies[-1], ensure_ascii=False)
    assert "CURRENT" in json.dumps(bodies[-1]) and "sent-1" in json.dumps(bodies[-1])
    assert bodies[-1][field][1]["role"] == "user"
    assert "历史背景摘要" in bodies[-1][field][1]["content"]
    assert "旧群聊内容" not in json.dumps(bodies[-1], ensure_ascii=False)
    assert (
        (bodies[-1].get("reasoning_effort") == "high")
        if protocol == "chat_completions"
        else (bodies[-1].get("reasoning") == {"effort": "high"})
    )


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_compaction_keeps_recent_protocol_group_and_excludes_reasoning(protocol):
    bodies = []
    normal = 0

    def respond(request):
        nonlocal normal
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" not in body:
            assert "private-reasoning" not in json.dumps(body)
            assert "opaque-old" not in json.dumps(body)
            return httpx.Response(200, json=answer(protocol, "旧查询已完成，结果已读取。"))
        normal += 1
        if normal == 1:
            return httpx.Response(200, json=call_answer(protocol, "old", reasoning="opaque-old"))
        if normal == 2:
            return httpx.Response(200, json=call_answer(protocol, "recent"))
        return httpx.Response(200, json=answer(protocol))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(protocol=protocol, model="test", max_context_tokens=8192),
            "system",
            "CURRENT",
            client=client,
        )
        old = await session.step([])
        session.result(old.calls[0], ToolResult.success({"data": "a" * 5000}), 10000)
        recent = await session.step([])
        session.result(recent.calls[0], ToolResult.success({"value": "small"}), 1000)
        session.add_background("历史" * 2000)
        await session.step([])
    field = "messages" if protocol == "chat_completions" else "input"
    final = bodies[-1][field]
    serialized = json.dumps(final)
    assert "opaque-old" not in serialized
    assert "private-reasoning" in serialized
    if protocol == "chat_completions":
        assert any(item.get("tool_call_id") == "recent" for item in final)
        assert any(item.get("tool_calls", [{}])[0].get("id") == "recent" for item in final)
        assert not any(item.get("tool_call_id") == "old" for item in final)
    else:
        assert any(
            item.get("call_id") == "recent" and item.get("type") == "function_call"
            for item in final
        )
        assert any(
            item.get("call_id") == "recent" and item.get("type") == "function_call_output"
            for item in final
        )
        assert any(item.get("id") == "r-recent" for item in final)
        assert not any(item.get("call_id") == "old" for item in final)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_usage_counts_cached_input_once_and_reasoning_output_once(protocol):
    usage = (
        {
            "prompt_tokens": 2500,
            "completion_tokens": 900,
            "prompt_tokens_details": {"cached_tokens": 2400},
            "completion_tokens_details": {"reasoning_tokens": 800},
        }
        if protocol == "chat_completions"
        else {
            "input_tokens": 2500,
            "output_tokens": 900,
            "input_tokens_details": {"cached_tokens": 2400},
            "output_tokens_details": {"reasoning_tokens": 800},
        }
    )
    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(
            200,
            json=answer(
                protocol,
                "背景已归纳" if "tools" not in body else "ok",
                **({"usage": usage} if len(bodies) == 1 else {}),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(protocol=protocol, model="test", max_context_tokens=4096),
            "system",
            "current",
            client=client,
        )
        session.add_background("context " * 160)
        await session.step([])
        assert session.context_state["input_tokens"] == 3400
        assert session.context_state["usage_source"] == "api_calibrated"
        await session.step([])
        assert session.context_state["compactions"] == 1
    assert "tools" not in bodies[1]


@pytest.mark.parametrize("invalid", ["empty", "tools", "truncated", "no_shrink"])
@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_summary_failure_preserves_original_context(protocol, invalid):
    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert "tools" not in body
        data = answer(
            protocol,
            "" if invalid == "empty" else "z" * 12000 if invalid == "no_shrink" else "摘要",
        )
        if invalid == "tools":
            data = call_answer(protocol, "forbidden")
        if invalid == "truncated":
            if protocol == "chat_completions":
                data["choices"][0]["finish_reason"] = "length"
            else:
                data = answer(protocol, "摘要", status="incomplete")
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(protocol=protocol, model="test", max_context_tokens=4096),
            "system",
            "CURRENT",
            client=client,
        )
        session.add_background("old " * 2000)
        session.request_limit = 20
        original = copy.deepcopy(session.items)
        with pytest.raises(CompactionError):
            await session.step([])
        assert session.items == original
        assert session.request_count == len(bodies)
        assert session.context_state["compactions"] == 0


async def test_summary_cancellation_is_atomic():
    async def respond(request):
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(model="test", max_context_tokens=4096),
            "system",
            "CURRENT",
            client=client,
        )
        session.add_background("old " * 2000)
        original = copy.deepcopy(session.items)
        with pytest.raises(asyncio.CancelledError):
            await session.step([])
        assert session.items == original and session.request_count == 1


async def test_fixed_current_and_tool_schemas_fail_without_any_http_request():
    def respond(request):
        pytest.fail("固定区域过大时不得发送请求")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(model="test", max_context_tokens=4096),
            "system",
            "CURRENT " * 2000,
            client=client,
        )
        session.add_background("small old")
        original = copy.deepcopy(session.items)
        with pytest.raises(ContextLimitError):
            await session.step([])
        assert session.items == original and session.request_count == 0
        session = ModelSession(
            ProviderSettings(model="test", max_context_tokens=4096),
            "system",
            "current",
            client=client,
        )
        with pytest.raises(ContextLimitError):
            await session.step([{"name": "huge", "description": "schema " * 2000}])


async def test_large_image_uses_independent_budget_and_is_preserved():
    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=answer("responses"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(model="test", max_context_tokens=16384),
            "system",
            "current",
            client=client,
        )
        session.add_media([MediaInput("image", "image/png", "a" * 4_000_000)])
        await session.step([])
        tokens = session.context_state["input_tokens"]
        assert isinstance(tokens, int) and tokens < 10000
    image = bodies[0]["input"][2]["content"][1]
    assert image["image_url"].endswith("a" * 100) and len(image["image_url"]) > 4_000_000


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_explicit_overflow_compacts_once_and_never_replays_tools(protocol):
    bodies = []
    normal = 0

    def respond(request):
        nonlocal normal
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" not in body:
            return httpx.Response(200, json=answer(protocol, "查询已完成。"))
        normal += 1
        if normal == 1:
            return httpx.Response(200, json=call_answer(protocol, "done"))
        return httpx.Response(
            400,
            json={
                "error": {"code": "context_length_exceeded", "message": "secret upstream content"}
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(protocol=protocol, model="test", max_context_tokens=8192),
            "system",
            "current",
            client=client,
        )
        reply = await session.step([])
        session.result(reply.calls[0], ToolResult.success({"value": "x" * 2000}), 3000)
        session.action_state = {"executed_calls": [{"call_id": "done", "status": "success"}]}
        with pytest.raises(ContextWindowExceeded, match="输入上下文") as error:
            await session.step([])
        assert "secret" not in str(error.value)
        assert session.context_state["compactions"] == 1
        assert session.request_count == 4
    assert normal == 3
    assert "executed_calls" in json.dumps(bodies[-1])


@pytest.mark.parametrize(
    "status,code,message",
    [
        (400, "invalid_request", "invalid parameter"),
        (413, "too_large", "payload too large"),
        (400, "invalid_value", "max_tokens exceeds output limit"),
        (401, "context_length_exceeded", "maximum context length"),
        (429, "context_length_exceeded", "maximum context length"),
        pytest.param(400, "context_length_exceeded", "x" * 70_000, id="oversized-error-body"),
    ],
)
async def test_unrelated_errors_do_not_trigger_compaction(status, code, message):
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(status, json={"error": {"code": code, "message": message}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(ProviderSettings(model="test"), "system", "current", client=client)
        session.add_background("background")
        with pytest.raises(ProviderError, match=f"HTTP {status}"):
            await session.step([])
        assert session.request_count == 1
    assert len(bodies) == 1


async def test_summary_requests_share_turn_limit_and_keep_original_on_partial_failure():
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=answer("responses", "摘要"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(model="test", max_context_tokens=4096),
            "system",
            "current",
            client=client,
        )
        session.request_limit = 1
        session.add_background("历史" * 5000)
        original = copy.deepcopy(session.items)
        with pytest.raises(ModelRequestLimit):
            await session.step([])
        assert session.request_count == 1 and session.items == original
    assert len(bodies) == 1


async def test_incomplete_tool_group_cannot_be_sent_or_summarized():
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=call_answer("responses", "pending"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(ProviderSettings(model="test"), "system", "current", client=client)
        await session.step([])
        session.add_background("more background")
        with pytest.raises(ProviderError, match="尚未完整回填"):
            await session.step([])
        assert session.request_count == 1
    assert len(bodies) == 1


async def test_usage_calibration_can_correct_conservative_text_overestimate():
    def respond(request):
        return httpx.Response(
            200,
            json=answer(
                "responses",
                "ok",
                usage={
                    "input_tokens": 1000,
                    "output_tokens": 25,
                    "input_tokens_details": {"cached_tokens": 800},
                },
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(ProviderSettings(model="test"), "system", "a" * 6000, client=client)
        await session.step([])
        assert session.context_state["input_tokens"] == 1025
        assert session.context_state["usage_source"] == "api_calibrated"


async def test_high_fixed_occupancy_can_summarize_recent_group_as_a_whole():
    bodies = []
    normal = 0

    def respond(request):
        nonlocal normal
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" not in body:
            return httpx.Response(200, json=answer("responses", "保留的背景结论"))
        normal += 1
        return httpx.Response(
            200, json=answer("responses", "old-answer-" + "a" * 1000 if normal == 1 else "done")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(model="test", max_context_tokens=4096),
            "system",
            "CURRENT-" + "a" * 5000,
            client=client,
        )
        await session.step([])
        session.add_background("history " * 300)
        await session.step([])
        assert session.context_state["compactions"] == 1
    final = json.dumps(bodies[-1])
    assert "CURRENT-" in final and "old-answer-" not in final
    assert estimate_tokens(bodies[-1]) < 4096 * 80 // 100


@pytest.mark.parametrize(
    "protocol,reason",
    [
        ("chat_completions", "length"),
        ("chat_completions", "content_filter"),
        ("responses", "incomplete"),
        ("responses", "failed"),
        ("responses", "cancelled"),
    ],
)
async def test_incomplete_normal_response_does_not_append_or_execute_calls(protocol, reason):
    def respond(request):
        data = call_answer(protocol, "must-not-execute")
        if protocol == "chat_completions":
            data["choices"][0]["finish_reason"] = reason
        else:
            data = {**data, "status": reason}
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(
            ProviderSettings(protocol=protocol, model="test"), "system", "current", client=client
        )
        original = copy.deepcopy(session.items)
        with pytest.raises(ProviderError):
            await session.step([])
        assert session.items == original and session.request_count == 1


async def test_responses_failed_context_error_in_http_success_can_recover_once():
    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                200,
                json={
                    "status": "failed",
                    "error": {"code": "context_length_exceeded", "message": "private input"},
                },
            )
        return httpx.Response(
            200, json=answer("responses", "历史背景已缩减" if "tools" not in body else "done")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        session = ModelSession(ProviderSettings(model="test"), "system", "current", client=client)
        session.add_background("background " * 300)
        reply = await session.step([])
        assert reply.text == "done" and session.request_count == 3
    assert "tools" not in bodies[1] and "tools" in bodies[2]
