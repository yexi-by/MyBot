import asyncio
from abc import ABCMeta, abstractmethod
from typing import ClassVar, cast

from app.api import BOTClient
from app.database import RedisDatabaseManager
from app.models import AllEvent
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding
from config import Settings
from app.utils import logger

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


class BasePlugin[T: AllEvent](metaclass=PluginMeta):
    name: ClassVar[str]
    consumers_count: ClassVar[int]
    priority: ClassVar[int]

    def __init__(
        self,
        llm: LLMHandler,
        siliconflow: SiliconFlowEmbedding,
        search_vectors: SearchVectors,
        bot: BOTClient,
        database: RedisDatabaseManager,
        settings:Settings
    ) -> None:
        self.llm = llm
        self.siliconflow = siliconflow
        self.search_vectors = search_vectors
        self.bot = bot
        self.database = database
        self.settings=settings
        self.task_queue: asyncio.Queue[tuple[T, asyncio.Future[bool]]] = asyncio.Queue()
        self.consumers: list[asyncio.Task] = []
        self.register_consumers()
        self.setup()

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
