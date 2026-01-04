import asyncio
from abc import ABCMeta, abstractmethod
from typing import ClassVar, cast, Callable, Any

from app.api import BOTClient
from app.database import RedisDatabaseManager
from app.models import AllEvent
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding, ContextHandler
from config import Settings
from app.utils import logger
from app.core.plugin_manager import PluginController
from dataclasses import dataclass

PLUGINS: list[type["BasePlugin"]] = []


class PluginMeta(ABCMeta):
    def __new__(mcs, name, bases, attrs):
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
            PLUGINS.append(cast(type["BasePlugin"], cls))
        return cls

@dataclass
class Context:
    llm: LLMHandler
    siliconflow: SiliconFlowEmbedding
    search_vectors: SearchVectors
    bot: BOTClient
    database: RedisDatabaseManager
    settings: Settings
    llm_context_handler:ContextHandler

class BasePlugin[T: AllEvent](metaclass=PluginMeta):
    name: ClassVar[str]
    consumers_count: ClassVar[int]
    priority: ClassVar[int]

    def __init__(
        self,
        context: Context,
    ) -> None:
        self.context = context
        self.task_queue: asyncio.Queue[tuple[T, asyncio.Future[bool]]] = asyncio.Queue()
        self.consumers: list[asyncio.Task] = []
        self.controller: PluginController | None = None
        self._pending_listeners: list[tuple[str, Callable]] = []
        self.register_consumers()
        self.setup()

    def set_controller(self, controller: PluginController) -> None:
        self.controller = controller
        for event, func in self._pending_listeners:
            self.controller.register_listener(event_name=event, callback=func)

    async def emit(self, event_name: str, **kwargs) -> Any:
        if self.controller:
            results = await self.controller.broadcast(
                event_name=event_name, kwargs=kwargs
            )
            if results:
                return results

    async def add_to_queue(self, msg: T) -> bool:
        """对外接口"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        await self.task_queue.put((msg, future))
        return await future

    async def consumer(self) -> None:
        while True:
            data, future = await self.task_queue.get()
            try:
                result = await self.run(msg=data)
                future.set_result(result)
            except Exception as e:
                logger.error(e)
                future.set_result(True)  # 强制消费链结束
            finally:
                self.task_queue.task_done()

    def register_consumers(self) -> None:
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self.consumer())
            self.consumers.append(consumer)

    async def stop_consumers(self) -> None:
        for consumer in self.consumers:
            consumer.cancel()
        if self.consumers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.consumers, return_exceptions=True), timeout=3
                )
            except asyncio.TimeoutError:
                logger.error(f"{self.name}插件消费者关闭超时")
            finally:
                self.consumers.clear()

    @abstractmethod
    def setup(self) -> None:
        pass

    @abstractmethod
    async def run(self, msg: T) -> bool:
        pass
