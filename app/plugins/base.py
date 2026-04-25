"""插件基类、插件注册元类和插件运行上下文。"""

from __future__ import annotations

import asyncio
from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Awaitable, Callable
from typing import ClassVar, Protocol, cast

import httpx

from app.api import BOTClient
from app.database import RedisDatabaseManager
from app.models import AllEvent
from app.services import LLMHandler, MCPToolManager
from app.config import Settings
from app.utils.log import log_event, log_exception


class PluginControllerProtocol(Protocol):
    """插件控制器在插件基类中需要使用的最小接口。"""

    def register_listener(
        self, event_name: str, callback: Callable[..., Awaitable[object]]
    ) -> None:
        """注册插件内部事件监听器。"""

    async def broadcast(
        self, event_name: str, kwargs: dict[str, object]
    ) -> list[object | BaseException] | None:
        """广播插件内部事件并返回监听器结果。"""


PLUGINS: list[type["BasePlugin[AllEvent]"]] = []


class PluginMeta(ABCMeta):
    """在插件类定义时校验元信息并完成自动注册。"""

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, object],
    ) -> type:
        """创建插件类并在定义期完成元信息校验。"""
        cls = super().__new__(mcs, name, bases, attrs)
        if bases and name != "BasePlugin":
            plugin_name = getattr(cls, "name", None)
            consumers_count = getattr(cls, "consumers_count", None)
            priority = getattr(cls, "priority", None)
            if "__init__" in attrs:
                raise ValueError(
                    f"{name}插件不允许重写 __init__ 方法,请使用 setup 方法"
                )
            if not plugin_name:
                raise ValueError(f"{name}插件缺少 name 属性,请重新定义")
            if consumers_count is None:
                raise ValueError(
                    f"{name}插件缺少 consumers_count(最大并发数量) 属性,请重新定义"
                )
            if priority is None:
                raise ValueError(f"{name}插件缺少 priority(优先级) 属性,请重新定义")
            for plugin in PLUGINS:
                if plugin.name == plugin_name:
                    raise ValueError(
                        f"已存在同名插件: {plugin_name},请修改插件 name 属性"
                    )
            PLUGINS.append(cast(type["BasePlugin[AllEvent]"], cls))
        return cls


class Context:
    """插件运行时上下文，集中暴露可注入服务。"""

    def __init__(
        self,
        settings: Settings,
        bot: BOTClient,
        database: RedisDatabaseManager,
        direct_httpx: httpx.AsyncClient,
        proxy_httpx: httpx.AsyncClient | None = None,
        llm: LLMHandler | None = None,
        mcp_tool_manager: MCPToolManager | None = None,
    ) -> None:
        """保存插件运行期可用服务。"""
        self.settings: Settings = settings
        self.bot: BOTClient = bot
        self.database: RedisDatabaseManager = database
        self.direct_httpx: httpx.AsyncClient = direct_httpx
        self._llm: LLMHandler | None = llm
        self._mcp_tool_manager: MCPToolManager | None = mcp_tool_manager
        self._proxy_httpx: httpx.AsyncClient | None = proxy_httpx

    @property
    def llm(self) -> LLMHandler:
        """返回 LLM 服务，未配置时显式报错。"""
        if self._llm is None:
            raise RuntimeError("上下文中的 LLMHandler 未正确初始化，请检查配置文件。")
        return self._llm

    @property
    def proxy_httpx(self) -> httpx.AsyncClient:
        """返回代理 HTTP 客户端，未配置时显式报错。"""
        if self._proxy_httpx is None:
            raise RuntimeError("上下文中的 AsyncClient 未正确初始化，请检查代理配置。")
        return self._proxy_httpx

    @property
    def mcp_tool_manager(self) -> MCPToolManager:
        """返回 MCP 工具管理器。"""
        if self._mcp_tool_manager is None:
            raise RuntimeError("上下文中的 MCPToolManager 未正确初始化。")
        return self._mcp_tool_manager


class BasePlugin[T: AllEvent](ABC, metaclass=PluginMeta):
    """所有插件的异步队列消费基类。"""

    name: ClassVar[str]
    consumers_count: ClassVar[int]
    priority: ClassVar[int]

    def __init__(
        self,
        context: Context,
    ) -> None:
        """初始化插件上下文、任务队列和消费者。"""
        self.context: Context = context
        self.task_queue: asyncio.Queue[tuple[T, asyncio.Future[bool]]] = asyncio.Queue()
        self.consumers: list[asyncio.Task[None]] = []
        self.controller: PluginControllerProtocol | None = None
        self._pending_listeners: list[
            tuple[str, Callable[..., Awaitable[object]]]
        ] = []
        self.register_consumers()
        self.setup()

    def pending_listeners(self) -> list[tuple[str, Callable[..., Awaitable[object]]]]:
        """返回插件 setup 阶段暂存的内部事件监听器。"""
        return list(self._pending_listeners)

    def set_controller(self, controller: PluginControllerProtocol) -> None:
        """绑定插件控制器并补注册 setup 阶段声明的监听器。"""
        self.controller = controller
        for event, func in self._pending_listeners:
            self.controller.register_listener(event_name=event, callback=func)

    async def emit(self, event_name: str, **kwargs: object) -> object | None:
        """向插件内部事件总线发送事件。"""
        if self.controller:
            results = await self.controller.broadcast(
                event_name=event_name, kwargs=kwargs
            )
            if results:
                return results

    async def add_to_queue(self, msg: T) -> bool:
        """将事件放入插件队列并等待消费结果。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        await self.task_queue.put((msg, future))
        return await future

    async def consumer(self) -> None:
        """持续消费插件事件队列。"""
        while True:
            data, future = await self.task_queue.get()
            try:
                result = await self.run(msg=data)
                future.set_result(result)
            except Exception as e:
                log_exception(
                    event="plugin.consumer.exception",
                    category="plugin",
                    message="插件处理事件失败",
                    exc=e,
                    plugin_name=self.name,
                    event_model=type(data).__name__,
                )
                future.set_exception(e)
            finally:
                self.task_queue.task_done()

    def register_consumers(self) -> None:
        """启动插件消费者任务。"""
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self.consumer())
            self.consumers.append(consumer)

    async def stop_consumers(self) -> None:
        """取消并停止插件消费者任务。"""
        for consumer in self.consumers:
            _ = consumer.cancel()
        if self.consumers:
            try:
                _ = await asyncio.wait_for(
                    asyncio.gather(*self.consumers, return_exceptions=True), timeout=3
                )
            except asyncio.TimeoutError:
                log_event(
                    level="ERROR",
                    event="plugin.consumer.stop_timeout",
                    category="plugin",
                    message="插件消费者关闭超时",
                    plugin_name=self.name,
                )
            finally:
                self.consumers.clear()

    @abstractmethod
    def setup(self) -> None:
        """注册插件内部状态与监听器。"""
        raise NotImplementedError

    @abstractmethod
    async def run(self, msg: T) -> bool:
        """处理指定事件并返回是否终止后续插件链。"""
        raise NotImplementedError
