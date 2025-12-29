import asyncio
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, fields
from typing import ClassVar, cast

from models import AllEvent

from app.api import BOTClient
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding

PLUGINS: list[type["BasePlugin"]] = []


@dataclass
class PluginContext:
    llm: LLMHandler
    siliconflow: SiliconFlowEmbedding
    search_vectors: SearchVectors
    bot: BOTClient


class PluginMeta(ABCMeta):
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        if bases:
            plugin_name = getattr(cls, "name", None)
            consumers_count = getattr(cls, "consumers_count", None)
            priority = getattr(cls, "priority", None)
            if "__init__" in attrs:
                raise ValueError("插件不允许重写 __init__ 方法,请使用 setup 方法")
            if not plugin_name:
                raise ValueError("插件缺少 name 属性,请重新定义")
            if consumers_count is None:
                raise ValueError(
                    "插件缺少 consumers_count(最大并发数量) 属性,请重新定义"
                )
            if priority is None:
                raise ValueError("插件缺少 priority(优先级) 属性,请重新定义")
            for plugin in PLUGINS:
                if plugin.name == plugin_name:
                    raise ValueError(
                        f"已存在同名插件: {plugin_name},请修改插件 name 属性"
                    )
            PLUGINS.append(cast(type["BasePlugin"], cls))
        return cls


class BasePlugin(metaclass=PluginMeta):
    llm: LLMHandler
    siliconflow: SiliconFlowEmbedding
    search_vectors: SearchVectors
    bot: BOTClient
    name: ClassVar[str]
    consumers_count: ClassVar[int]
    priority: ClassVar[int]

    def __init__(self, context: PluginContext) -> None:
        self.context = context
        self.task_queue: asyncio.Queue[tuple[AllEvent, asyncio.Future[bool]]] = (
            asyncio.Queue()
        )
        self.consumers: list[asyncio.Task] = []
        self.register_consumers()
        for field in fields(context):
            name = field.name
            instance = getattr(context, name)
            setattr(self, name, instance)
        self.setup()

    async def add_to_queue(self, data: AllEvent) -> bool:
        """对外接口"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        await self.task_queue.put((data, future))
        return await future

    async def consumer(self) -> None:
        while True:
            data, future = await self.task_queue.get()
            result = await self.run(data=data)
            future.set_result(result)
            self.task_queue.task_done()

    def register_consumers(self) -> None:
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self.consumer())
            self.consumers.append(consumer)

    @abstractmethod
    def setup(self) -> None:
        pass

    @abstractmethod
    async def run(self, data: AllEvent) -> bool:
        pass


# 待实现 死锁,异常捕捉
