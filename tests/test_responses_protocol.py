"""Responses 请求格式、无状态工具续问及失败边界回归。"""

import base64
import json
import unittest
from typing import cast
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from app.config import LLMProviderConfig, NetworkConfig
from app.models import JsonObject
from app.services.llm.errors import LLMRequestError, LLMResponseError
from app.services.llm.handler import LLMHandler
from app.services.llm.providers.responses import ResponsesService
from app.services.llm.schemas import ChatMessage, LLMProviderWrapper, LLMToolCall, LLMToolDefinition
from app.services.llm.wrapper import ResilientLLMProvider
from tests.test_openai_chat_formatting import PNG_1X1_BYTES


def completed_response(output: list[dict[str, object]], **changes: object) -> dict[str, object]:
    """构造真实 SDK 可读取的 Responses HTTP 响应。"""
    return {
        "id": "resp-test", "object": "response", "created_at": 0,
        "model": "same-model", "status": "completed", "output": output,
        "error": None, "incomplete_details": None, **changes,
    }


def output_message(text: str) -> dict[str, object]:
    return {
        "type": "message", "id": "msg-test", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def provider_config(api_format: str) -> LLMProviderConfig:
    return LLMProviderConfig.model_validate({
        "api_format": api_format, "base_url": "https://model.invalid/v1",
        "inherit_network_proxy": False, "timeout_seconds": 1,
        "max_attempts": 3, "retry_delay_seconds": 0, "retry_max_delay_seconds": 0,
    })


class ResponsesProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_rounds_replay_all_output_items_without_duplicate_messages(self) -> None:
        """多轮工具续问保留输出顺序与加密状态，展示只取正式 message 内容。"""
        outputs: list[list[dict[str, object]]] = [
            [
                {
                    "type": "reasoning", "id": "rs-1",
                    "summary": [{"type": "summary_text", "text": "私有规划"}],
                    "encrypted_content": "encrypted-first",
                },
                output_message("我先查询库存。"),
                {
                    "type": "function_call", "id": "fc-1", "call_id": "lookup-1",
                    "name": "lookup", "arguments": '{"item":"book"}', "status": "completed",
                },
            ],
            [
                {
                    "type": "reasoning", "id": "rs-2", "summary": [],
                    "encrypted_content": "encrypted-second",
                },
                {
                    "type": "function_call", "id": "fc-2", "call_id": "lookup-2",
                    "name": "lookup", "arguments": '{"item":"pen"}', "status": "completed",
                },
            ],
            [output_message("库存已确认。")],
        ]
        requests: list[dict[str, object]] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/responses")
            requests.append(cast(dict[str, object], json.loads(request.content)))
            return httpx.Response(200, json=completed_response(outputs[len(requests) - 1]))

        async with AsyncOpenAI(
            api_key="test", base_url="https://model.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            service = ResponsesService(client)
            tool = LLMToolDefinition(name="lookup", description="查库存", parameters={})
            messages = [ChatMessage(role="user", text="查库存")]
            first = await service.get_ai_response_with_tools(messages, "same-model", [tool])
            self.assertEqual(first.content, "我先查询库存。")
            self.assertEqual(first.tool_calls[0].id, "lookup-1")
            self.assertEqual(first.tool_calls[0].arguments, {"item": "book"})
            self.assertEqual(first.response_items, outputs[0])
            messages.extend([
                ChatMessage(role="assistant", text=first.content, tool_calls=first.tool_calls,
                            response_items=first.response_items),
                ChatMessage(role="tool", tool_call_id="lookup-1", text='{"count":3}'),
            ])
            second = await service.get_ai_response_with_tools(
                messages, "same-model", [tool],
                tool_choice={"type": "function", "function": {"name": "lookup"}},
            )
            self.assertIsNone(second.content)
            messages.extend([
                ChatMessage(role="assistant", tool_calls=second.tool_calls,
                            response_items=second.response_items),
                ChatMessage(role="tool", tool_call_id="lookup-2", text='{"count":4}'),
            ])
            final = await service.get_ai_response_with_tools(messages, "same-model", [tool], tool_choice="none")
            self.assertEqual(final.content, "库存已确认。")

        first_result = {"type": "function_call_output", "call_id": "lookup-1", "output": '{"count":3}'}
        second_result = {"type": "function_call_output", "call_id": "lookup-2", "output": '{"count":4}'}
        expected = [{"role": "user", "content": "查库存"}, *outputs[0], first_result]
        self.assertEqual(requests[1]["input"], expected)
        self.assertEqual(requests[2]["input"], [*expected, *outputs[1], second_result])
        self.assertEqual(requests[1]["tool_choice"], {"type": "function", "name": "lookup"})
        self.assertEqual(requests[2]["tool_choice"], "none")
        for request in requests:
            self.assertIs(request["store"], False)
            self.assertEqual(request["include"], ["reasoning.encrypted_content"])
            self.assertNotIn("previous_response_id", request)
            self.assertEqual(request["tools"], [{
                "type": "function", "name": "lookup", "description": "查库存",
                "parameters": {}, "strict": True,
            }])
            self.assertFalse({"temperature", "top_p", "max_tokens", "max_output_tokens"} & request.keys())

    async def test_text_and_images_use_responses_content_shapes(self) -> None:
        """图片直接按 input_image 交付，历史助手正文和函数结果使用对应 Item 格式。"""
        captured: list[dict[str, object]] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            captured.append(cast(dict[str, object], json.loads(request.content)))
            return httpx.Response(200, json=completed_response([output_message("看到了。")]))

        async with AsyncOpenAI(
            api_key="test", base_url="https://model.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            result = await ResponsesService(client).get_ai_response([
                ChatMessage(role="system", text="简洁回答"),
                ChatMessage(role="assistant", text="之前的回答", tool_calls=[
                    LLMToolCall(id="historic-call", name="lookup", arguments={"key": "value"})
                ]),
                ChatMessage(role="tool", tool_call_id="historic-call", text="历史结果"),
                ChatMessage(role="user", text="看看图片", image=[PNG_1X1_BYTES], image_detail="high"),
            ], "same-model")
        self.assertEqual(result, "看到了。")
        self.assertEqual(captured[0]["input"], [
            {"role": "system", "content": "简洁回答"},
            {"role": "assistant", "content": "之前的回答"},
            {"type": "function_call", "call_id": "historic-call", "name": "lookup", "arguments": '{"key": "value"}'},
            {"type": "function_call_output", "call_id": "historic-call", "output": "历史结果"},
            {"role": "user", "content": [
                {"type": "input_text", "text": "看看图片"},
                {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(PNG_1X1_BYTES).decode(), "detail": "high"},
            ]},
        ])
        self.assertEqual(set(captured[0]), {"model", "input", "store", "include"})

    async def test_incomplete_or_invalid_outputs_fail_without_retry(self) -> None:
        """不完整响应和坏工具调用不能交给执行器，也不会触发追加指令。"""
        function: dict[str, object] = {
            "type": "function_call", "call_id": "call-1", "name": "lookup", "arguments": "{}",
        }
        cases = [
            completed_response([output_message("截断内容")], status="incomplete"),
            completed_response([output_message("失败内容")], error={"code": "server_error", "message": "secret"}),
            completed_response([]),
            completed_response([{**output_message("截断条目"), "status": "incomplete"}]),
            completed_response([{**function, "arguments": "[]"}]),
            completed_response([{**function, "arguments": "{"}]),
            completed_response([{**function, "call_id": ""}]),
            completed_response([{**function, "name": ""}]),
            completed_response([{key: value for key, value in function.items() if key != "call_id"}]),
            completed_response([function, function]),
            completed_response([output_message("<think>私有内容</think>")]),
            completed_response([{"type": "reasoning", "id": "rs-only", "summary": [], "encrypted_content": "hidden"}]),
            completed_response([{"type": "image_generation_call", "id": "image-1", "status": "completed", "result": "hidden"}]),
        ]
        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                requests = 0

                async def respond(request: httpx.Request) -> httpx.Response:
                    nonlocal requests
                    requests += 1
                    return httpx.Response(200, json=payload)

                async with AsyncOpenAI(
                    api_key="test", base_url="https://model.invalid/v1", max_retries=0,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                ) as client:
                    provider = ResilientLLMProvider(ResponsesService(client), provider_config("responses"))
                    with self.assertRaises(LLMResponseError):
                        await provider.get_ai_response_with_tools(
                            [ChatMessage(role="user", text="测试")], "same-model",
                            [LLMToolDefinition(name="lookup", description="查询", parameters={})],
                        )
                self.assertEqual(requests, 1)

    async def test_video_input_reports_unsupported_before_network_request(self) -> None:
        """没有已定义的 Responses 视频协议时明确失败，不静默漏掉视频。"""
        async def unexpected_request(request: httpx.Request) -> httpx.Response:
            self.fail("不支持的视频输入不应发起请求")

        async with AsyncOpenAI(
            api_key="test", base_url="https://model.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)),
        ) as client:
            handler = LLMHandler(services={"main": LLMProviderWrapper(
                provider_id="main", provider=ResilientLLMProvider(ResponsesService(client), provider_config("responses")),
            )}, clients=[])
            with self.assertRaisesRegex(LLMRequestError, "Responses 接口不支持当前视频输入格式"):
                await handler.get_ai_text_response(
                    [ChatMessage(role="user", video=[b"video"])], "main", "same-model"
                )

    async def test_unrequested_function_calls_are_rejected(self) -> None:
        """纯文本请求、无工具请求和禁止工具选择都不能接受函数调用。"""
        async def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=completed_response([{
                "type": "function_call", "call_id": "unexpected", "name": "lookup", "arguments": "{}",
            }]))

        async with AsyncOpenAI(
            api_key="test", base_url="https://model.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            service = ResponsesService(client)
            for mode in ("text", "no_tools", "none"):
                with self.subTest(mode=mode), self.assertRaisesRegex(LLMResponseError, "没有提供可调用工具"):
                    messages = [ChatMessage(role="user", text="只要文字")]
                    if mode == "text":
                        await service.get_ai_response(messages, "same-model")
                    else:
                        tools = [LLMToolDefinition(name="lookup", description="查询", parameters={})] if mode == "none" else []
                        await service.get_ai_response_with_tools(messages, "same-model", tools, tool_choice="none")

    async def test_api_format_selects_endpoint_without_model_name_branching(self) -> None:
        """相同模型名只因 provider 的 api_format 不同而使用不同请求协议。"""
        paths: list[str] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            body = cast(JsonObject, json.loads(request.content))
            self.assertEqual(body["model"], "same-model")
            if request.url.path.endswith("/responses"):
                return httpx.Response(200, json=completed_response([output_message("Responses")]))
            return httpx.Response(200, json={
                "id": "chat-test", "object": "chat.completion", "created": 0, "model": "same-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Chat Completions"}}],
            })

        clients = [AsyncOpenAI(
            api_key="test", base_url="https://model.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) for _ in range(2)]
        with patch("app.services.llm.handler.AsyncOpenAI", side_effect=clients):
            handler = LLMHandler.register_instance(
                {"chat": provider_config("chat_completions"), "responses": provider_config("responses")},
                NetworkConfig(timeout_seconds=1),
            )
        try:
            message = [ChatMessage(role="user", text="你好")]
            self.assertEqual(await handler.get_ai_text_response(message, "chat", "same-model"), "Chat Completions")
            self.assertEqual(await handler.get_ai_text_response(message, "responses", "same-model"), "Responses")
        finally:
            await handler.aclose()
        self.assertEqual(paths, ["/v1/chat/completions", "/v1/responses"])
