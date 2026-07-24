"""MCP 子进程环境变量测试。"""

import os
import unittest
from unittest.mock import patch

from app.services.llm.mcp import build_mcp_server_environment


class MCPEnvironmentTest(unittest.TestCase):
    """验证 MCP 子进程安全继承运行环境中的代理配置。"""

    def test_inherits_only_proxy_environment(self) -> None:
        """代理变量会透传，其他容器变量不会泄露给 MCP 子进程。"""
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://proxy.example:8080",
                "npm_config_https_proxy": "http://proxy.example:8080",
                "UNRELATED_SECRET": "do-not-inherit",
            },
            clear=True,
        ):
            environment = build_mcp_server_environment(None)

        normalized_environment = {
            name.casefold(): value for name, value in (environment or {}).items()
        }
        self.assertEqual(
            normalized_environment,
            {
                "http_proxy": "http://proxy.example:8080",
                "npm_config_https_proxy": "http://proxy.example:8080",
            },
        )

    def test_configured_environment_overrides_proxy(self) -> None:
        """服务私有配置优先于容器中的同名代理变量。"""
        with patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://container-proxy.example:8080"},
            clear=True,
        ):
            environment = build_mcp_server_environment(
                {
                    "HTTPS_PROXY": "http://server-proxy.example:8080",
                    "FIRECRAWL_API_KEY": "test-key",
                }
            )

        self.assertEqual(
            environment,
            {
                "HTTPS_PROXY": "http://server-proxy.example:8080",
                "FIRECRAWL_API_KEY": "test-key",
            },
        )

    def test_returns_none_without_any_environment(self) -> None:
        """没有显式配置或代理时保持 MCP SDK 的默认继承行为。"""
        with patch.dict(os.environ, {}, clear=True):
            environment = build_mcp_server_environment(None)

        self.assertIsNone(environment)
