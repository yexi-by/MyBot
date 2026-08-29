"""WebUI 模型列表代理端点测试。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.webui import llm_models
from app.webui.dev import create_dev_app
from tests.config_helpers import minimal_config_toml


def _models_page(ids: list[str]) -> SimpleNamespace:
    """构造 OpenAI models.list 响应形态。"""
    return SimpleNamespace(data=[SimpleNamespace(id=model_id) for model_id in ids])


class WebUILLMModelsTest(unittest.IsolatedAsyncioTestCase):
    """验证 /api/llm/providers/{id}/models 的代理行为与错误语义。"""

    def setUp(self) -> None:
        llm_models.clear_models_cache()  # 缓存键含配置哈希，测试间仍显式隔离

    def _client(self, root: Path) -> httpx.AsyncClient:
        app = create_dev_app(config_file=root / "mybot.toml")
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://webui.test"
        )

    async def test_list_models_returns_sorted_ids(self) -> None:
        """正常拉取返回排序后的模型 id，并按配置构建客户端。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(minimal_config_toml(), encoding="utf-8")
            with patch("app.webui.llm_models.AsyncOpenAI") as mock_client_cls:
                instance = mock_client_cls.return_value
                instance.models.list = AsyncMock(
                    return_value=_models_page(["z-model", "a-model", "a-model"])
                )
                instance.close = AsyncMock()
                async with self._client(root) as client:
                    response = await client.get("/api/llm/providers/main/models")

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["provider"], "main")
            self.assertEqual(body["models"], ["a-model", "z-model"])
            _, kwargs = mock_client_cls.call_args
            self.assertEqual(kwargs["api_key"], "test-api-key")
            self.assertIsNone(kwargs["base_url"])
            self.assertEqual(kwargs["max_retries"], 0)
            instance.close.assert_awaited_once()

    async def test_list_models_unknown_provider_returns_404(self) -> None:
        """未定义的 provider 返回 404。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(minimal_config_toml(), encoding="utf-8")
            async with self._client(root) as client:
                response = await client.get("/api/llm/providers/ghost/models")

        self.assertEqual(response.status_code, 404)
        self.assertIn("ghost", response.json()["detail"])

    async def test_list_models_upstream_error_returns_502(self) -> None:
        """上游不可达返回 502 与脱敏原因。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(minimal_config_toml(), encoding="utf-8")
            with patch("app.webui.llm_models.AsyncOpenAI") as mock_client_cls:
                instance = mock_client_cls.return_value
                instance.models.list = AsyncMock(
                    side_effect=ConnectionError("connection refused")
                )
                instance.close = AsyncMock()
                async with self._client(root) as client:
                    response = await client.get("/api/llm/providers/main/models")

            self.assertEqual(response.status_code, 502)
            detail = response.json()["detail"]
            self.assertIn("拉取模型列表失败", detail)
            self.assertNotIn("test-api-key", detail)

    async def test_list_models_cached_within_ttl(self) -> None:
        """30 秒 TTL 内同配置同 provider 不重复请求上游。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(minimal_config_toml(), encoding="utf-8")
            with patch("app.webui.llm_models.AsyncOpenAI") as mock_client_cls:
                instance = mock_client_cls.return_value
                instance.models.list = AsyncMock(
                    return_value=_models_page(["cached-model"])
                )
                instance.close = AsyncMock()
                async with self._client(root) as client:
                    first = await client.get("/api/llm/providers/main/models")
                    second = await client.get("/api/llm/providers/main/models")

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["models"], ["cached-model"])
            self.assertEqual(mock_client_cls.call_count, 1)


if __name__ == "__main__":
    unittest.main()
