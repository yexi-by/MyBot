"""NapCatServer 应用级资源生命周期回归测试。"""

import asyncio
import unittest
from typing import cast

from dishka import AsyncContainer
from fastapi import FastAPI

from app.config import ConfigWatcher, MyBotConfig
from app.core.di import DirectHttpx, ProxyHttpx
from app.core.server import NapCatServer
from app.database import (
    DatabaseMigrator,
    PostgreSQLMessageRepository,
    PostgreSQLRuntime,
)
from app.services import LLMHandler, MCPToolManager
from app.services.napcat import ImageArchiveWorkerFactory


class _ClosableResource:
    """记录资源关闭，并可模拟关闭失败。"""

    def __init__(self, *, fail_on_close: bool = False) -> None:
        self.closed = False
        self.fail_on_close = fail_on_close

    async def aclose(self) -> None:
        """记录关闭动作。"""
        self.closed = True
        if self.fail_on_close:
            raise RuntimeError("close failed")


class _FakeRuntime:
    """只实现 lifespan 使用的 PostgreSQL runtime 方法。"""

    def __init__(self) -> None:
        self.checked = False
        self.disposed = False

    async def check_connection(self) -> None:
        """记录连接检查。"""
        self.checked = True

    async def dispose(self) -> None:
        """记录连接池释放。"""
        self.disposed = True


class _FakeMigrator:
    """只实现启动版本检查。"""

    async def assert_current(self) -> None:
        """模拟 migration 已是最新。"""


class _FakeMCPManager:
    """记录 MCP 启停，并可模拟关闭失败。"""

    def __init__(self, *, fail_on_close: bool = False) -> None:
        self.started = False
        self.closed = False
        self.fail_on_close = fail_on_close

    async def start(self) -> None:
        """记录启动。"""
        self.started = True

    async def close(self) -> None:
        """记录关闭动作。"""
        self.closed = True
        if self.fail_on_close:
            raise RuntimeError("mcp close failed")


class _FakeConfigWatcher:
    """记录配置 watcher 是否完成停止。"""

    def __init__(self) -> None:
        self.stop_event = asyncio.Event()
        self.stopped = False

    async def run(self) -> None:
        """等待生命周期通知停止。"""
        await self.stop_event.wait()
        self.stopped = True

    def stop(self) -> None:
        """通知 run 返回。"""
        self.stop_event.set()


class _FakeContainer:
    """按依赖键返回 lifespan 所需的 fake 对象。"""

    def __init__(self, values: dict[object, object], *, fail_key: object | None = None):
        self.values = values
        self.fail_key = fail_key
        self.closed = False

    async def get(self, dependency: object) -> object:
        """模拟 Dishka get；指定依赖可在解析阶段失败。"""
        if dependency == self.fail_key:
            raise RuntimeError("dependency resolution failed")
        return self.values[dependency]

    async def close(self) -> None:
        """记录容器关闭。"""
        self.closed = True


class NapCatServerLifespanTest(unittest.IsolatedAsyncioTestCase):
    """验证启动失败和关闭失败都不会漏掉已创建资源。"""

    def _config(self) -> MyBotConfig:
        """构造不依赖本机配置文件的最小设置。"""
        return MyBotConfig.model_validate(
            {
                "napcat": {"websocket_token": "test-token"},
                "database": {"password": "test-password"},
            }
        )

    def _server(self, *, container: _FakeContainer) -> NapCatServer:
        """绕过路由注册，只测试 lifespan 本身。"""
        server = object.__new__(NapCatServer)
        server.container = cast(AsyncContainer, cast(object, container))
        server.config = self._config()
        return server

    def _resources(
        self, *, mcp_close_fails: bool = False
    ) -> tuple[dict[object, object], _FakeRuntime, _FakeMCPManager, _ClosableResource]:
        """构造一组可观察的应用级资源。"""
        runtime = _FakeRuntime()
        mcp = _FakeMCPManager(fail_on_close=mcp_close_fails)
        direct_httpx = _ClosableResource()
        resources: dict[object, object] = {
            PostgreSQLRuntime: runtime,
            DatabaseMigrator: _FakeMigrator(),
            MCPToolManager: mcp,
            DirectHttpx: direct_httpx,
            ProxyHttpx | None: None,
            ConfigWatcher: _FakeConfigWatcher(),
            PostgreSQLMessageRepository: object(),
            ImageArchiveWorkerFactory: object(),
            LLMHandler | None: None,
        }
        return resources, runtime, mcp, direct_httpx

    async def test_one_close_failure_does_not_skip_remaining_resources(self) -> None:
        """单个资源关闭报错后，HTTP、数据库和容器仍必须关闭。"""
        resources, runtime, mcp, direct_httpx = self._resources(
            mcp_close_fails=True
        )
        container = _FakeContainer(resources)
        server = self._server(container=container)

        with self.assertRaises(BaseExceptionGroup):
            async with server.lifespan(cast(FastAPI, cast(object, None))):
                pass

        self.assertTrue(mcp.started)
        self.assertTrue(mcp.closed)
        self.assertTrue(direct_httpx.closed)
        self.assertTrue(runtime.disposed)
        self.assertTrue(container.closed)

    async def test_dependency_resolution_failure_closes_created_runtime(self) -> None:
        """后续依赖解析失败时，先前创建的连接池仍会释放。"""
        resources, runtime, _, _ = self._resources()
        container = _FakeContainer(resources, fail_key=DatabaseMigrator)
        server = self._server(container=container)

        with self.assertRaisesRegex(RuntimeError, "dependency resolution failed"):
            async with server.lifespan(cast(FastAPI, cast(object, None))):
                pass

        self.assertTrue(runtime.disposed)
        self.assertTrue(container.closed)


if __name__ == "__main__":
    unittest.main()
