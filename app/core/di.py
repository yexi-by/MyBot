"""依赖注入容器配置。"""

from typing import ClassVar, NewType

import httpx
from dishka import Provider, Scope, from_context
from dishka import provide as provide  # pyright: ignore[reportUnknownVariableType]
from fastapi import WebSocket
from redis.asyncio import Redis

from app.api import BOTClient
from app.config import Settings
from app.database import RedisDatabaseManager
from app.models import AllEvent
from app.plugins import PLUGINS, BasePlugin, Context, load_all_plugins
from app.services import LLMHandler, MCPToolManager

from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker
from .plugin_manager import PluginController

DirectHttpx = NewType("DirectHttpx", httpx.AsyncClient)
ProxyHttpx = NewType("ProxyHttpx", httpx.AsyncClient)


class MyProvider(Provider):
    """声明应用运行所需的依赖对象。"""

    # Dishka 的 from_context 返回提供器占位对象，第三方类型无法表达为 WebSocket 实例。
    websocket: ClassVar[object] = from_context(provides=WebSocket, scope=Scope.SESSION)

    def __init__(self, settings: Settings) -> None:
        """保存启动阶段已经校验过的全局配置。"""
        super().__init__()
        self.settings = settings

    @provide(scope=Scope.APP)
    def get_settings(self) -> Settings:
        """读取并构造全局配置。"""
        return self.settings

    @provide(scope=Scope.APP)
    def get_direct_httpx(self, settings: Settings) -> DirectHttpx:
        """创建不带代理的 HTTP 客户端。"""
        return DirectHttpx(httpx.AsyncClient(timeout=settings.network.timeout_seconds))

    @provide(scope=Scope.APP)
    def get_event_type_checker(self) -> EventTypeChecker:
        """创建事件类型解析器。"""
        return EventTypeChecker()

    @provide(scope=Scope.APP)
    def get_proxy_httpx(self, settings: Settings) -> ProxyHttpx | None:
        """初始化可选代理 HTTP 客户端。"""
        proxy = settings.network.proxy
        if proxy is None:
            return None
        return ProxyHttpx(
            httpx.AsyncClient(proxy=proxy, timeout=settings.network.timeout_seconds)
        )

    @provide(scope=Scope.APP)
    def get_llm_handler(self, settings: Settings) -> LLMHandler | None:
        """初始化可选 LLM 服务。"""
        if not settings.llm.providers:
            return None
        return LLMHandler.register_instance(settings.llm.providers)

    @provide(scope=Scope.APP)
    def get_mcp_tool_manager(self, settings: Settings) -> MCPToolManager:
        """创建 MCP 工具管理器。"""
        return MCPToolManager(settings.mcp)

    @provide(scope=Scope.APP)
    def get_redis_client(self, settings: Settings) -> Redis:
        """创建 Redis 异步客户端。"""
        return Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.db,
            password=settings.redis.password,
        )

    @provide(scope=Scope.APP)
    def get_redis_database_manager(
        self, redis_client: Redis, settings: Settings, client: DirectHttpx
    ) -> RedisDatabaseManager:
        """创建 Redis 消息存储服务。"""
        path = settings.storage.media_path
        return RedisDatabaseManager(redis_client=redis_client, path=path, client=client)

    @provide(scope=Scope.SESSION)
    def get_bot_client(
        self, websocket: WebSocket, database: RedisDatabaseManager
    ) -> BOTClient:
        """创建当前 WebSocket 会话的机器人客户端。"""
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
        mcp_tool_manager: MCPToolManager,
    ) -> PluginController:
        """实例化插件控制器。"""
        load_all_plugins()
        plugin_objects: list[BasePlugin[AllEvent]] = []
        for cls in PLUGINS:
            context = Context(
                settings=settings,
                bot=bot,
                database=database,
                llm=llm,
                mcp_tool_manager=mcp_tool_manager,
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
        """创建会话事件分发器。"""
        return EventDispatcher(plugincontroller=plugincontroller, bot=bot)
