"""LLM 单次请求重试覆盖测试。"""

import unittest
import traceback
from typing import override
from unittest.mock import AsyncMock, call, patch

import httpx
from openai import AuthenticationError
from pydantic import SecretStr

from app.config import LLMProviderConfig, NetworkConfig
from app.services.llm.base import LLMProvider
from app.services.llm.handler import LLMHandler
from app.services.llm.schemas import ChatMessage, LLMProviderWrapper
from app.services.llm.wrapper import ResilientLLMProvider
from app.services.llm.errors import LLMRequestError


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
            raise ValueError("临时视觉响应错误")
        return "视觉描述成功"


class LLMRequestRetryTest(unittest.IsolatedAsyncioTestCase):
    """验证当前请求可以替换供应商默认重试参数。"""

    async def test_provider_error_omits_credentials_from_feedback_and_exception_chain(self) -> None:
        inner = FlakyTextProvider(failures_before_success=0)
        provider = ResilientLLMProvider(inner, LLMProviderConfig(
            api_key=SecretStr("test-key"), inherit_network_proxy=True, timeout_seconds=0,
            max_attempts=1, retry_delay_seconds=0, retry_max_delay_seconds=0,
        ))
        handler = LLMHandler(services={"main": LLMProviderWrapper(provider_id="main", provider=provider)}, clients=[])
        error = AuthenticationError(
            "invalid test-key at https://user:secret@model.invalid",
            response=httpx.Response(401, request=httpx.Request("GET", "https://model.invalid")),
            body={"error": "test-key"},
        )
        with patch.object(inner, "get_image", side_effect=error):
            try:
                await handler.get_image(ChatMessage(role="user", text="test"), model="image", provider="main")
            except LLMRequestError as exc:
                formatted = "".join(traceback.format_exception(exc))
                self.assertIn("HTTP 401", str(exc))
                self.assertNotIn("test-key", formatted)
                self.assertNotIn("user:secret", formatted)
            else:
                self.fail("模型鉴权失败应返回脱敏异常")

    async def test_register_instance_allows_provider_without_api_key(self) -> None:
        """无鉴权 OpenAI 兼容服务使用空 key，客户端不会发送认证头。"""
        provider_config = LLMProviderConfig.model_validate(
            {
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

    async def test_request_override_retries_three_attempts_with_backoff(
        self,
    ) -> None:
        """覆盖值 3 表示总尝试三次，并按一秒、两秒指数退避。"""
        inner_provider = FlakyTextProvider(failures_before_success=2)
        provider = ResilientLLMProvider(
            inner_provider=inner_provider,
            provider_config=LLMProviderConfig.model_validate(
                {
                    "api_key": "test-key",
                    "inherit_network_proxy": True,
                    "timeout_seconds": 0,
                    "max_attempts": 1,
                    "retry_delay_seconds": 9,
                    "retry_max_delay_seconds": 10,
                }
            ),
        )
        handler = LLMHandler(
            services={
                "vision-vendor": LLMProviderWrapper(
                    provider_id="vision-vendor",
                    provider=provider,
                )
            },
            clients=[],
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await handler.get_ai_text_response(
                messages=[ChatMessage(role="user", text="看图")],
                provider="vision-vendor",
                model_name="vision-model",
                max_attempts=3,
                retry_delay_seconds=1,
                retry_max_delay_seconds=0,
            )

        self.assertEqual(result, "视觉描述成功")
        self.assertEqual(inner_provider.call_count, 3)
        self.assertEqual(sleep.await_args_list, [call(1.0), call(2.0)])

    async def test_zero_delay_retries_immediately(self) -> None:
        """零延迟配置不会被固定的一秒退避覆盖。"""
        inner_provider = FlakyTextProvider(failures_before_success=2)
        provider = ResilientLLMProvider(
            inner_provider=inner_provider,
            provider_config=LLMProviderConfig.model_validate(
                {
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

        self.assertEqual(result, "视觉描述成功")
        self.assertEqual(inner_provider.call_count, 3)
        self.assertEqual(sleep.await_args_list, [call(0.0), call(0.0)])


if __name__ == "__main__":
    unittest.main()
