import asyncio
from abc import ABCMeta, abstractmethod
from typing import ClassVar, cast, Callable, Any
from functools import wraps
from app.api import BOTClient
from app.database import RedisDatabaseManager
from app.models import AllEvent
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding, ContextHandler
from app.services.ai_image import NaiClient
from config import Settings
from app.utils import logger
from app.core.plugin_manager import PluginController
import httpx

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


def require_initialized(func):
    """
    属性装饰器：
    1. 自动检查返回值是否为 None。
    2. 如果是 None，自动通过反射读取函数定义的返回类型，抛出详细错误。
    """
    prop_name = func.__name__
    hints = func.__annotations__
    return_type = hints.get("return")
    if return_type is None:
        raise ValueError(f"无法获取属性 {prop_name} 的返回类型注解")
    type_name = getattr(return_type, "__name__", str(return_type))

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        instance = func(self, *args, **kwargs)
        if instance is None:
            raise RuntimeError(
                f"上下文中的 {type_name} 未正确初始化, 请检查配置文件中是否填写了相关字段。"
            )
        return instance

    return wrapper


class Context:
    def __init__(
        self,
        settings: Settings,
        bot: BOTClient,
        database: RedisDatabaseManager,
        direct_httpx: httpx.AsyncClient,
        proxy_httpx: httpx.AsyncClient | None = None,
        llm: LLMHandler | None = None,
        siliconflow: SiliconFlowEmbedding | None = None,
        search_vectors: SearchVectors | None = None,
        nai_client: NaiClient | None = None,
    ):
        self.settings = settings
        self.bot = bot
        self.database = database
        self.direct_httpx = direct_httpx
        self._llm = llm
        self._siliconflow = siliconflow
        self._search_vectors = search_vectors
        self._nai_client = nai_client
        self._proxy_httpx = proxy_httpx

    @property
    @require_initialized
    def llm(self) -> LLMHandler:
        return self._llm  # type: ignore

    @property
    @require_initialized
    def siliconflow(self) -> SiliconFlowEmbedding:
        return self._siliconflow  # type: ignore

    @property
    @require_initialized
    def search_vectors(self) -> SearchVectors:
        return self._search_vectors  # type: ignore

    @property
    @require_initialized
    def llm_context_handler(self) -> ContextHandler:
        return self._llm_context_handler  # type: ignore

    @property
    @require_initialized
    def nai_client(self) -> NaiClient:
        return self._nai_client  # type: ignore

    @property
    @require_initialized
    def proxy_httpx(self) -> httpx.AsyncClient:
        return self._proxy_httpx  # type: ignore


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
