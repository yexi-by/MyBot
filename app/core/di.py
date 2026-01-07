import inspect
from functools import wraps
from typing import NewType, get_type_hints

import httpx
from dishka import Provider, Scope, from_context, provide
from fastapi import WebSocket
from redis.asyncio import Redis

from app.api import BOTClient
from app.database import RedisDatabaseManager
from app.plugins import PLUGINS, BasePlugin, Context
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding
from app.services.ai_image import NaiClient
from app.utils import logger
from app.config import RAW_CONFIG_DICT, Settings

from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker
from .plugin_manager import PluginController


def optional_parameters(func):
    """
    装饰器：捕获初始化异常并返回 None。
    自动兼容 sync 和 async 函数。
    """
    hints = get_type_hints(func)
    return_type = hints.get("return")
    if return_type is None:
        return_type = "unknown"
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"可选组件初始化失败 [{return_type}]: {e}，将跳过。")
                return None

        return async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"可选组件初始化失败 [{return_type}]: {e}，将跳过。")
                return None

        return sync_wrapper


DirectHttpx = NewType("DirectHttpx", httpx.AsyncClient)
ProxyHttpx = NewType("ProxyHttpx", httpx.AsyncClient)


class MyProvider(Provider):
    websocket = from_context(provides=WebSocket, scope=Scope.SESSION)

    @provide(scope=Scope.APP)
    def get_settings(self) -> Settings:
        return Settings(**RAW_CONFIG_DICT)

    @provide(scope=Scope.APP)
    def get_direct_httpx(self) -> DirectHttpx:
        return DirectHttpx(httpx.AsyncClient())

    @provide(scope=Scope.APP)
    def get_event_type_checker(
        self,
    ) -> EventTypeChecker:
        return EventTypeChecker()

    # 下面是可选项
    @provide(scope=Scope.APP)
    @optional_parameters
    def get_proxy_httpx(self, settings: Settings) -> ProxyHttpx | None:
        proxy = settings.proxy
        return ProxyHttpx(httpx.AsyncClient(proxy=proxy))


    @provide(scope=Scope.APP)
    @optional_parameters
    def get_llm_handler(self, settings: Settings) -> LLMHandler | None:
        return LLMHandler.register_instance(settings.llm_settings)

    @provide(scope=Scope.APP)
    @optional_parameters
    def get_siliconflow_embedding(
        self, settings: Settings, client: DirectHttpx
    ) -> SiliconFlowEmbedding | None:
        assert settings.embedding_settings is not None
        return SiliconFlowEmbedding(
            embedding_config=settings.embedding_settings,
            client=client,
        )

    @provide(scope=Scope.APP)
    @optional_parameters
    async def get_search_vectors(self, settings: Settings) -> SearchVectors | None:
        return await SearchVectors.create_from_directory(
            directory=settings.faiss_file_location
        )

    @provide(scope=Scope.APP)
    @optional_parameters
    def get_nai_client(self, settings: Settings, client: DirectHttpx) -> NaiClient | None:
        nai_config = settings.nai_settings
        if nai_config is None:
            raise ValueError("nai_settings is not configured")
        return NaiClient(
            client=client,
            url=nai_config.base_url,
            api_key=nai_config.api_key,
        )

    # 可选服务结束
    @provide(scope=Scope.APP)
    def get_redis_client(self, settings: Settings) -> Redis:
        return Redis(
            host=settings.redis_config.host,
            port=settings.redis_config.port,
            db=settings.redis_config.db,
            password=settings.redis_config.password,
        )

    @provide(scope=Scope.APP)
    def get_redis_database_manager(
        self, redis_client: Redis, settings: Settings, client: DirectHttpx
    ) -> RedisDatabaseManager:
        path = settings.video_and_image_path
        return RedisDatabaseManager(redis_client=redis_client, path=path, client=client)

    @provide(scope=Scope.SESSION)
    def get_bot_client(
        self, websocket: WebSocket, database: RedisDatabaseManager
    ) -> BOTClient:
        return BOTClient(websocket=websocket, database=database)

    @provide(scope=Scope.SESSION)
    def get_plugin_controller(
        self,
        bot: BOTClient,
        database: RedisDatabaseManager,
        settings: Settings,
        directhttpx: DirectHttpx,
        proxy_httpx: ProxyHttpx | None,
        llm: LLMHandler | None,
        siliconflow: SiliconFlowEmbedding | None,
        search_vectors: SearchVectors | None,
        nai_client: NaiClient | None,
    ) -> PluginController:
        logger.info(f"创建 PluginController，PLUGINS 列表长度: {len(PLUGINS)}, 内容: {PLUGINS}")
        plugin_objects: list[BasePlugin] = []
        for cls in PLUGINS:
            context = Context(
                settings=settings,
                bot=bot,
                database=database,
                llm=llm,
                siliconflow=siliconflow,
                search_vectors=search_vectors,
                nai_client=nai_client,
                direct_httpx=directhttpx,
                proxy_httpx=proxy_httpx,
            )
            plugin_object = cls(context=context)
            plugin_objects.append(plugin_object)
        return PluginController(plugin_objects=plugin_objects)

    @provide(scope=Scope.SESSION)
    def get_event_dispatcher(
        self, plugincontroller: PluginController, bot: BOTClient
    ) -> EventDispatcher:
        return EventDispatcher(plugincontroller=plugincontroller, bot=bot)
