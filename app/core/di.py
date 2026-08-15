"""依赖注入容器配置。"""

from pathlib import Path
from typing import ClassVar, NewType

import httpx
from dishka import Provider, Scope, from_context
from dishka import provide as provide  # pyright: ignore[reportUnknownVariableType]
from fastapi import WebSocket

from app.api import BOTClient
from app.config import Settings
from app.database import (
    DatabaseMigrator,
    PluginMigrationRegistry,
    PluginRepositoryBuilder,
    PostgreSQLMessageRepository,
    PostgreSQLRuntime,
)
from app.models import AllEvent
from app.plugins import (
    PLUGINS,
    BasePlugin,
    Context,
    discover_plugin_migrations,
    load_all_plugins,
)
from app.services import LLMHandler, MCPToolManager
from app.services.napcat import (
    ImageArchiveWorkerFactory,
    ImageStore,
    InlineImageArchiver,
)

from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker
from .plugin_manager import PluginController

DirectHttpx = NewType("DirectHttpx", httpx.AsyncClient)
ProxyHttpx = NewType("ProxyHttpx", httpx.AsyncClient)
PostgreSQLUrl = NewType("PostgreSQLUrl", str)


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
    def get_database_url(self, settings: Settings) -> PostgreSQLUrl:
        """构造不会被手工拼接破坏的 PostgreSQL DSN。"""
        return PostgreSQLUrl(settings.database.build_url())

    @provide(scope=Scope.APP)
    def get_postgresql_runtime(
        self,
        database_url: PostgreSQLUrl,
        settings: Settings,
    ) -> PostgreSQLRuntime:
        """创建 PostgreSQL engine 和短生命周期 session factory。"""
        config = settings.database
        return PostgreSQLRuntime.create(
            database_url=database_url,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout_seconds=config.pool_timeout_seconds,
            statement_timeout_seconds=config.statement_timeout_seconds,
        )

    @provide(scope=Scope.APP)
    def get_group_message_repository(
        self,
        runtime: PostgreSQLRuntime,
        settings: Settings,
    ) -> PostgreSQLMessageRepository:
        """创建群消息、撤回和图片任务共用的 PostgreSQL repository。"""
        return PostgreSQLMessageRepository(
            session_factory=runtime.session_factory,
            image_root=Path(settings.storage.image_path).resolve(),
        )

    @provide(scope=Scope.APP)
    def get_plugin_repository_builder(
        self,
        runtime: PostgreSQLRuntime,
    ) -> PluginRepositoryBuilder:
        """创建只按 plugin_id 构造类型化 repository 的入口。"""
        return PluginRepositoryBuilder(engine=runtime.engine)

    @provide(scope=Scope.APP)
    def get_plugin_migration_registry(self) -> PluginMigrationRegistry:
        """收集当前启用插件实际声明的 migration。"""
        registry = PluginMigrationRegistry()
        for migration in discover_plugin_migrations():
            registry.register(migration)
        return registry

    @provide(scope=Scope.APP)
    def get_database_migrator(
        self,
        database_url: PostgreSQLUrl,
        plugin_registry: PluginMigrationRegistry,
    ) -> DatabaseMigrator:
        """创建只检查版本、不会在应用启动时改表的 migration 服务。"""
        return DatabaseMigrator(
            database_url=database_url,
            plugin_registry=plugin_registry,
        )

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
    def get_image_store(self, settings: Settings) -> ImageStore:
        """创建内容寻址的群图片文件存储。"""
        return ImageStore(
            root=Path(settings.storage.image_path).resolve(),
            max_image_bytes=settings.storage.image_max_bytes,
        )

    @provide(scope=Scope.APP)
    def get_image_archive_worker_factory(
        self,
        repository: PostgreSQLMessageRepository,
        direct_httpx: DirectHttpx,
        image_store: ImageStore,
        settings: Settings,
    ) -> ImageArchiveWorkerFactory:
        """创建按 NapCat 会话绑定机器人身份的图片归档 worker 工厂。"""
        storage = settings.storage
        return ImageArchiveWorkerFactory(
            repository=repository,
            http_client=direct_httpx,
            store=image_store,
            concurrency=storage.image_download_concurrency,
            download_timeout_seconds=storage.image_download_timeout_seconds,
            max_image_bytes=storage.image_max_bytes,
            lease_seconds=storage.image_lease_seconds,
            retry_delays_seconds=storage.image_retry_delays_seconds,
        )

    @provide(scope=Scope.APP)
    def get_inline_image_archiver(
        self,
        image_store: ImageStore,
    ) -> InlineImageArchiver:
        """创建出站 base64 图片的主动归档服务。"""
        return InlineImageArchiver(store=image_store)

    @provide(scope=Scope.SESSION)
    def get_bot_client(
        self,
        websocket: WebSocket,
        repository: PostgreSQLMessageRepository,
        inline_image_archiver: InlineImageArchiver,
        settings: Settings,
    ) -> BOTClient:
        """创建当前 WebSocket 会话的机器人客户端。"""
        return BOTClient(
            websocket=websocket,
            sent_message_recorder=repository,
            inline_image_archiver=inline_image_archiver,
            send_retry_count=settings.napcat.send_retry_count,
            send_retry_delay=settings.napcat.send_retry_delay,
        )

    @provide(scope=Scope.SESSION)
    def get_plugin_controller(
        self,
        bot: BOTClient,
        repository: PostgreSQLMessageRepository,
        repository_builder: PluginRepositoryBuilder,
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
                group_messages=repository,
                plugin_id=cls.plugin_id,
                repository_builder=repository_builder,
                llm=llm,
                mcp_tool_manager=mcp_tool_manager,
                direct_httpx=directhttpx,
                proxy_httpx=proxy_httpx,
            )
            plugin_objects.append(cls(context=context))
        return PluginController(plugin_objects=plugin_objects)

    @provide(scope=Scope.SESSION)
    def get_event_dispatcher(
        self, plugincontroller: PluginController, bot: BOTClient
    ) -> EventDispatcher:
        """创建会话事件分发器。"""
        return EventDispatcher(plugincontroller=plugincontroller, bot=bot)
