"""LLM 协议边界及 provider 重试策略测试。"""

import json
import unittest
import traceback
from typing import cast, override
from unittest.mock import AsyncMock, call, patch

import httpx
from openai import APIConnectionError, AsyncOpenAI, AuthenticationError
from pydantic import SecretStr

from app.config import LLMProviderConfig, NetworkConfig
from app.models import JsonObject
from app.services.llm.base import LLMProvider
from app.services.llm.handler import LLMHandler
from app.services.llm.schemas import ChatMessage, LLMProviderWrapper
from app.services.llm.wrapper import ResilientLLMProvider
from app.services.llm.errors import LLMRequestError, LLMResponseError
from app.services.llm.providers.chat_completions import ChatCompletionsService
from app.services.llm.schemas import LLMToolDefinition


class FlakyTextProvider(LLMProvider):
    """在指定次数内抛出可重试错误，然后返回文本。"""

    def __init__(self, *, failures_before_success: int) -> None:
        """保存成功前的失败次数。"""
        self.failures_before_success = failures_before_success
        self.call_count = 0

    @override
    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> str:
        """记录请求并按计划失败。"""
        _ = (messages, model)
        self.call_count += 1
        if self.call_count <= self.failures_before_success:
            raise APIConnectionError(request=httpx.Request("POST", "https://model.invalid"))
        return "文本响应成功"


class LLMRequestRetryTest(unittest.IsolatedAsyncioTestCase):
    """验证 provider 配置控制网络重试，协议异常不会改写请求。"""

    async def test_invalid_protocol_response_fails_without_rewriting_request(self) -> None:
        """通过真实协议适配器拒绝异常响应，不重试或追加模型指令。"""
        rejected_marker = "private-rejected-payload"
        valid_call: dict[str, object] = {
            "id": "call-recovered", "type": "function",
            "function": {"name": "inspect", "arguments": '{"limit": 15}'},
        }
        malformed_call: dict[str, object] = {
            **valid_call,
            "function": {"name": "inspect", "arguments": f'{{"secret":"{rejected_marker}"'},
        }
        invalid_cases: list[tuple[str, str, dict[str, object], str]] = [
            ("text_call", "text", {"content": f"<call:default_api:inspect>{rejected_marker}</call:default_api:inspect>"}, "stop"),
            ("no_tools_thought", "no_tools", {"content": f"<thought\n>{rejected_marker}</thought>公开回复"}, "stop"),
            ("thought_header", "text", {"content": f"🫧thought\n{rejected_marker}公开回复"}, "stop"),
            ("bare_thought_header", "tools", {"content": f"💭\nLet's check the repository. {rejected_marker}", "tool_calls": [valid_call]}, "tool_calls"),
            ("embedded_thought", "tools", {"content": f"公开片段🫧thought\n{rejected_marker}", "tool_calls": [valid_call]}, "tool_calls"),
            ("quoted_thought_header", "no_tools", {"content": f"`💭thinking`\n{rejected_marker}"}, "stop"),
            ("channel_marker", "text", {"content": f"<|channel|>analysis\n{rejected_marker}"}, "stop"),
            ("invalid_tool_state", "tools", {"tool_calls": [{**valid_call, "extra_content": rejected_marker}]}, "tool_calls"),
            ("invalid_refusal", "text", {"refusal": {"message": rejected_marker}}, "stop"),
            ("malformed_arguments", "tools", {"tool_calls": [malformed_call]}, "tool_calls"),
            ("non_object_arguments", "tools", {"tool_calls": [{**valid_call, "function": {"name": "inspect", "arguments": "[]"}}]}, "tool_calls"),
            ("truncated_content", "text", {"content": rejected_marker}, "length"),
            ("filtered_content", "text", {"content": rejected_marker}, "content_filter"),
            ("missing_calls", "tools", {"content": rejected_marker}, "tool_calls"),
            ("missing_call_id", "tools", {"tool_calls": [{key: value for key, value in valid_call.items() if key != "id"}]}, "tool_calls"),
            ("duplicate_call_ids", "tools", {"tool_calls": [valid_call, valid_call]}, "tool_calls"),
        ]
        for name, mode, invalid_message, finish_reason in invalid_cases:
            with self.subTest(case=name):
                request_messages: list[list[dict[str, object]]] = []

                async def respond(request: httpx.Request) -> httpx.Response:
                    body = cast(dict[str, object], json.loads(request.content))
                    request_messages.append(cast(list[dict[str, object]], body["messages"]))
                    first_attempt = len(request_messages) == 1
                    message: dict[str, object] = (
                        invalid_message if first_attempt else
                        {"tool_calls": [valid_call]} if mode == "tools" else
                        {"content": "恢复成功"}
                    )
                    choice: dict[str, object] = {
                        "index": 0,
                        "finish_reason": finish_reason if first_attempt else ("tool_calls" if name == "malformed_arguments" else "stop"),
                        "message": {"role": "assistant", **message},
                    }
                    return httpx.Response(200, json={
                        "id": "test", "object": "chat.completion", "created": 0,
                        "model": "test", "choices": [choice],
                    })

                async with AsyncOpenAI(
                    api_key="test", base_url="https://model.invalid/v1", max_retries=0,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                ) as client:
                    provider = ResilientLLMProvider(ChatCompletionsService(client), LLMProviderConfig(
                        api_format="chat_completions",
                        inherit_network_proxy=False, timeout_seconds=1, max_attempts=2,
                        retry_delay_seconds=0, retry_max_delay_seconds=0,
                    ))
                    messages = [ChatMessage(role="user", text="查看最近消息")]
                    original_messages = [message.model_copy(deep=True) for message in messages]
                    with self.assertRaises(LLMResponseError):
                        if mode == "text":
                            await provider.get_ai_response(messages, "test")
                        else:
                            tools = [LLMToolDefinition(name="inspect", description="查看消息", parameters={})] if mode == "tools" else []
                            await provider.get_ai_response_with_tools(messages, "test", tools)
                    self.assertEqual(messages, original_messages)

                self.assertEqual(len(request_messages), 1)
                self.assertNotIn(rejected_marker, json.dumps(request_messages))

    async def test_tool_signature_survives_without_extracting_reasoning_fields(self) -> None:
        """只续传工具签名，不提取、展示或回传上游扩展思考字段。"""
        requests: list[dict[str, object]] = []
        details: list[JsonObject] = [
            {"type": "reasoning.text", "text": "内部规划", "signature": "signed-detail"},
            {"type": "reasoning.encrypted", "data": "encrypted-state"},
        ]
        extra: JsonObject = {"google": {"thought_signature": "signed-tool-state"}}

        async def respond(request: httpx.Request) -> httpx.Response:
            requests.append(cast(dict[str, object], json.loads(request.content)))
            first = len(requests) == 1
            message: dict[str, object] = {
                "role": "assistant", "content": None,
                "reasoning_details": details,
                "tool_calls": [{
                    "id": "lookup-1", "type": "function", "extra_content": extra,
                    "function": {"name": "lookup", "arguments": '{"item":"book"}'},
                }],
            } if first else {"role": "assistant", "content": "查询结果已确认"}
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 0,
                "model": "test", "choices": [{
                    "index": 0, "finish_reason": "tool_calls" if first else "stop",
                    "message": message,
                }],
            })

        async with AsyncOpenAI(
            api_key="test", base_url="https://model.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            service = ChatCompletionsService(client)
            tools = [LLMToolDefinition(name="lookup", description="查询", parameters={})]
            messages = [ChatMessage(role="user", text="查询库存")]
            first = await service.get_ai_response_with_tools(messages, "test", tools)
            self.assertIsNone(first.content)
            self.assertEqual(first.response_items, [])
            self.assertNotIn("内部规划", first.model_dump_json())
            messages.extend([
                ChatMessage(
                    role="assistant", tool_calls=first.tool_calls,
                ),
                ChatMessage(role="tool", tool_call_id="lookup-1", text='{"count":3}'),
            ])
            final = await service.get_ai_response_with_tools(messages, "test", tools)

        replay = cast(list[dict[str, object]], requests[1]["messages"])[1]
        self.assertNotIn("reasoning_details", replay)
        self.assertNotIn("reasoning_content", replay)
        call_data = cast(list[dict[str, object]], replay["tool_calls"])[0]
        self.assertEqual(call_data["extra_content"], extra)
        self.assertIsNone(replay["content"])
        self.assertEqual(final.content, "查询结果已确认")

    async def test_official_refusal_is_delivered_as_formal_reply(self) -> None:
        """正式 refusal 字段是可交付回复，不会因 content 为空而被误报为空响应。"""
        refusal = "这个请求无法协助，但可以提供一般说明。"
        requests = 0

        async def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(200, json={
                "id": "refusal-test", "object": "chat.completion", "created": 0,
                "model": "test", "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": None, "refusal": refusal},
                }],
            })

        async with AsyncOpenAI(
            api_key="test", base_url="https://model.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            provider = ResilientLLMProvider(ChatCompletionsService(client), LLMProviderConfig(
                api_format="chat_completions", inherit_network_proxy=False,
                timeout_seconds=1, max_attempts=3, retry_delay_seconds=0, retry_max_delay_seconds=0,
            ))
            messages = [ChatMessage(role="user", text="测试问题")]
            self.assertEqual(await provider.get_ai_response(messages, "test"), refusal)
            for tools in ([], [LLMToolDefinition(name="lookup", description="查询", parameters={})]):
                response = await provider.get_ai_response_with_tools(messages, "test", tools)
                self.assertEqual(response.content, refusal)
                self.assertEqual(response.tool_calls, [])
        self.assertEqual(requests, 3)

    async def test_protocol_failure_keeps_error_and_request_clean(self) -> None:
        """协议异常立即结束请求，反馈和异常链不包含被拒绝正文。"""
        rejected_marker = "private-rejected-payload"
        for mode in ("text", "tools"):
            with self.subTest(mode=mode):
                request_messages: list[list[dict[str, object]]] = []

                async def respond(request: httpx.Request) -> httpx.Response:
                    body = cast(dict[str, object], json.loads(request.content))
                    request_messages.append(cast(list[dict[str, object]], body["messages"]))
                    return httpx.Response(200, json={
                        "id": "test", "object": "chat.completion", "created": 0,
                        "model": "test", "choices": [{
                            "index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": f"<think>{rejected_marker}</think>"},
                        }],
                    })

                async with AsyncOpenAI(
                    api_key="test", base_url="https://model.invalid/v1", max_retries=0,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                ) as client:
                    provider = ResilientLLMProvider(ChatCompletionsService(client), LLMProviderConfig(
                        api_format="chat_completions",
                        inherit_network_proxy=False, timeout_seconds=1, max_attempts=3,
                        retry_delay_seconds=0, retry_max_delay_seconds=0,
                    ))
                    handler = LLMHandler(services={"main": LLMProviderWrapper(
                        provider_id="main", provider=provider,
                    )}, clients=[])
                    messages = [ChatMessage(role="user", text="正常问题")]
                    original_messages = [message.model_copy(deep=True) for message in messages]
                    with self.assertRaises(LLMRequestError) as caught:
                        if mode == "text":
                            await handler.get_ai_text_response(messages, "main", "test")
                        else:
                            await handler.get_ai_response_with_tools(messages, "main", "test", [
                                LLMToolDefinition(name="inspect", description="查看消息", parameters={}),
                            ])
                    self.assertNotIn(rejected_marker, "".join(traceback.format_exception(caught.exception)))
                    self.assertEqual(messages, original_messages)

                self.assertEqual(len(request_messages), 1)
                self.assertNotIn(rejected_marker, json.dumps(request_messages))

    async def test_provider_error_omits_credentials_from_feedback_and_exception_chain(self) -> None:
        inner = FlakyTextProvider(failures_before_success=0)
        provider = ResilientLLMProvider(inner, LLMProviderConfig(
            api_format="chat_completions",
            api_key=SecretStr("test-key"), inherit_network_proxy=True, timeout_seconds=0,
            max_attempts=3, retry_delay_seconds=0, retry_max_delay_seconds=0,
        ))
        handler = LLMHandler(services={"main": LLMProviderWrapper(provider_id="main", provider=provider)}, clients=[])
        error = AuthenticationError(
            "invalid test-key at https://user:secret@model.invalid",
            response=httpx.Response(401, request=httpx.Request("GET", "https://model.invalid")),
            body={"error": "test-key"},
        )
        with patch.object(inner, "get_ai_response_with_tools", side_effect=error) as request:
            try:
                await handler.get_ai_response_with_tools(
                    [ChatMessage(role="user", text="test")],
                    model_name="chat", provider="main", tools=[],
                )
            except LLMRequestError as exc:
                formatted = "".join(traceback.format_exception(exc))
                self.assertIn("HTTP 401", str(exc))
                self.assertNotIn("test-key", formatted)
                self.assertNotIn("user:secret", formatted)
            else:
                self.fail("模型鉴权失败应返回脱敏异常")
        self.assertEqual(request.await_count, 1)

    async def test_empty_choices_fail_for_text_and_tool_requests(self) -> None:
        """缺失候选时三种调用入口均拒绝响应，不把结构错误作为网络错误重试。"""
        for mode in ("text", "no_tools", "tools"):
            with self.subTest(mode=mode):
                requests = 0

                async def respond(request: httpx.Request) -> httpx.Response:
                    nonlocal requests
                    requests += 1
                    return httpx.Response(200, json={
                        "id": "test", "object": "chat.completion", "created": 0,
                        "model": "test", "choices": [],
                    })

                async with AsyncOpenAI(
                    api_key="test", base_url="https://model.invalid/v1", max_retries=0,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                ) as client:
                    provider = ResilientLLMProvider(ChatCompletionsService(client), LLMProviderConfig(
                        api_format="chat_completions", inherit_network_proxy=False,
                        timeout_seconds=1, max_attempts=3, retry_delay_seconds=0, retry_max_delay_seconds=0,
                    ))
                    messages = [ChatMessage(role="user", text="测试")]
                    with self.assertRaisesRegex(LLMResponseError, "缺少候选"):
                        if mode == "text":
                            await provider.get_ai_response(messages, "test")
                        else:
                            tools = [LLMToolDefinition(name="inspect", description="查询", parameters={})] if mode == "tools" else []
                            await provider.get_ai_response_with_tools(messages, "test", tools)
                self.assertEqual(requests, 1)

    async def test_network_retry_keeps_original_payload_and_obeys_attempt_limit(self) -> None:
        """暂时服务错误按原请求重试，持续失败严格受配置次数限制。"""
        for recover in (True, False):
            with self.subTest(recover=recover):
                payloads: list[bytes] = []

                async def respond(request: httpx.Request) -> httpx.Response:
                    payloads.append(request.content)
                    if not recover or len(payloads) == 1:
                        return httpx.Response(503, json={"error": {"message": "temporary"}})
                    return httpx.Response(200, json={
                        "id": "test", "object": "chat.completion", "created": 0, "model": "test",
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "恢复成功"}}],
                    })

                async with AsyncOpenAI(
                    api_key="test", base_url="https://model.invalid/v1", max_retries=0,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                ) as client:
                    provider = ResilientLLMProvider(ChatCompletionsService(client), LLMProviderConfig(
                        api_format="chat_completions", inherit_network_proxy=False,
                        timeout_seconds=1, max_attempts=2, retry_delay_seconds=0, retry_max_delay_seconds=0,
                    ))
                    handler = LLMHandler(services={"main": LLMProviderWrapper(provider_id="main", provider=provider)}, clients=[])
                    messages = [ChatMessage(role="user", text="原始问题")]
                    if recover:
                        result = await handler.get_ai_response_with_tools(messages, "main", "test", [])
                        self.assertEqual(result.content, "恢复成功")
                    else:
                        with self.assertRaisesRegex(LLMRequestError, "HTTP 503"):
                            await handler.get_ai_response_with_tools(messages, "main", "test", [])
                self.assertEqual(len(payloads), 2)
                self.assertEqual(payloads[0], payloads[1])

    async def test_register_instance_allows_provider_without_api_key(self) -> None:
        """无鉴权 OpenAI 兼容服务使用空 key，客户端不会发送认证头。"""
        provider_config = LLMProviderConfig.model_validate(
            {
                "api_format": "chat_completions",
                "base_url": "http://model.internal/v1",
                "inherit_network_proxy": True,
                "timeout_seconds": 0,
                "max_attempts": 5,
                "retry_delay_seconds": 0,
                "retry_max_delay_seconds": 0,
            }
        )

        with patch("app.services.llm.handler.AsyncOpenAI") as client_type:
            handler = LLMHandler.register_instance(
                {"local": provider_config},
                NetworkConfig(proxy=None, timeout_seconds=15),
            )

        client_type.assert_called_once_with(
            api_key="",
            base_url="http://model.internal/v1",
            timeout=15,
            max_retries=0,
            http_client=None,
        )
        self.assertIn("local", handler.services)

    async def test_provider_configuration_retries_three_attempts_with_backoff(
        self,
    ) -> None:
        """配置的最大次数包含首次请求，并按一秒、两秒指数退避。"""
        inner_provider = FlakyTextProvider(failures_before_success=2)
        provider = ResilientLLMProvider(
            inner_provider=inner_provider,
            provider_config=LLMProviderConfig.model_validate(
                {
                    "api_format": "chat_completions",
                    "api_key": "test-key",
                    "inherit_network_proxy": True,
                    "timeout_seconds": 0,
                    "max_attempts": 3,
                    "retry_delay_seconds": 1,
                    "retry_max_delay_seconds": 0,
                }
            ),
        )
        handler = LLMHandler(
            services={
                "main": LLMProviderWrapper(
                    provider_id="main",
                    provider=provider,
                )
            },
            clients=[],
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await handler.get_ai_text_response(
                messages=[ChatMessage(role="user", text="正常问题")],
                provider="main",
                model_name="chat-model",
            )

        self.assertEqual(result, "文本响应成功")
        self.assertEqual(inner_provider.call_count, 3)
        self.assertEqual(sleep.await_args_list, [call(1.0), call(2.0)])

    async def test_zero_delay_retries_immediately(self) -> None:
        """零延迟配置不会被固定的一秒退避覆盖。"""
        inner_provider = FlakyTextProvider(failures_before_success=2)
        provider = ResilientLLMProvider(
            inner_provider=inner_provider,
            provider_config=LLMProviderConfig.model_validate(
                {
                    "api_format": "chat_completions",
                    "api_key": "test-key",
                    "inherit_network_proxy": True,
                    "timeout_seconds": 0,
                    "max_attempts": 3,
                    "retry_delay_seconds": 0,
                    "retry_max_delay_seconds": 0,
                }
            ),
        )
        handler = LLMHandler(
            services={
                "fast-vendor": LLMProviderWrapper(
                    provider_id="fast-vendor",
                    provider=provider,
                )
            },
            clients=[],
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await handler.get_ai_text_response(
                messages=[ChatMessage(role="user", text="立即重试")],
                provider="fast-vendor",
                model_name="fast-model",
            )

        self.assertEqual(result, "文本响应成功")
        self.assertEqual(inner_provider.call_count, 3)
        self.assertEqual(sleep.await_args_list, [call(0.0), call(0.0)])


if __name__ == "__main__":
    unittest.main()
