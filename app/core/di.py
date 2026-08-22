"""依赖注入容器配置。"""

from pathlib import Path
from typing import ClassVar, NewType

import httpx
from dishka import Provider, Scope, from_context
from dishka import provide as provide  # pyright: ignore[reportUnknownVariableType]
from fastapi import WebSocket

from app.api import BOTClient
from app.config import ConfigManager, ConfigWatcher, MyBotConfig
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
from app.services import ConversationContextStore, LLMHandler, MCPToolManager
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

    def __init__(self, config_manager: ConfigManager) -> None:
        """保存启动阶段已经校验过的统一配置。"""
        super().__init__()
        self.config_manager = config_manager

    @provide(scope=Scope.APP)
    def get_config_manager(self) -> ConfigManager:
        """返回统一配置管理器。"""
        return self.config_manager

    @provide(scope=Scope.APP)
    def get_config(self, manager: ConfigManager) -> MyBotConfig:
        """返回启动时固定的完整配置。"""
        return manager.boot_config

    @provide(scope=Scope.APP)
    def get_config_watcher(self, manager: ConfigManager) -> ConfigWatcher:
        """创建插件配置目录 watcher。"""
        return ConfigWatcher(manager=manager)

    @provide(scope=Scope.APP)
    def get_database_url(self, config: MyBotConfig) -> PostgreSQLUrl:
        """构造不会被手工拼接破坏的 PostgreSQL DSN。"""
        return PostgreSQLUrl(config.database.build_url())

    @provide(scope=Scope.APP)
    def get_postgresql_runtime(
        self,
        database_url: PostgreSQLUrl,
        config: MyBotConfig,
    ) -> PostgreSQLRuntime:
        """创建 PostgreSQL engine 和短生命周期 session factory。"""
        database = config.database
        return PostgreSQLRuntime.create(
            database_url=database_url,
            pool_size=database.pool_size,
            max_overflow=database.max_overflow,
            pool_timeout_seconds=database.pool_timeout_seconds,
            statement_timeout_seconds=database.statement_timeout_seconds,
        )

    @provide(scope=Scope.APP)
    def get_group_message_repository(
        self,
        runtime: PostgreSQLRuntime,
        config: MyBotConfig,
    ) -> PostgreSQLMessageRepository:
        """创建群消息、撤回和图片任务共用的 PostgreSQL repository。"""
        return PostgreSQLMessageRepository(
            session_factory=runtime.session_factory,
            image_root=Path(config.storage.images.directory).resolve(),
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
    def get_direct_httpx(self, config: MyBotConfig) -> DirectHttpx:
        """创建不带代理的 HTTP 客户端。"""
        return DirectHttpx(httpx.AsyncClient(timeout=config.network.timeout_seconds))

    @provide(scope=Scope.APP)
    def get_event_type_checker(self) -> EventTypeChecker:
        """创建事件类型解析器。"""
        return EventTypeChecker()

    @provide(scope=Scope.APP)
    def get_proxy_httpx(self, config: MyBotConfig) -> ProxyHttpx | None:
        """初始化可选代理 HTTP 客户端。"""
        proxy = config.network.proxy
        if proxy is None:
            return None
        return ProxyHttpx(
            httpx.AsyncClient(proxy=proxy, timeout=config.network.timeout_seconds)
        )

    @provide(scope=Scope.APP)
    def get_llm_handler(self, config: MyBotConfig) -> LLMHandler | None:
        """初始化可选 LLM 服务。"""
        if not config.llm.providers:
            return None
        return LLMHandler.register_instance(config.llm.providers)

    @provide(scope=Scope.APP)
    def get_mcp_tool_manager(self, config: MyBotConfig) -> MCPToolManager:
        """创建 MCP 工具管理器。"""
        return MCPToolManager(config.mcp)

    @provide(scope=Scope.APP)
    def get_conversation_context_store(self) -> ConversationContextStore:
        """创建当前进程内跨 NapCat 连接复用的对话上下文存储。"""
        return ConversationContextStore()

    @provide(scope=Scope.APP)
    def get_image_store(self, config: MyBotConfig) -> ImageStore:
        """创建内容寻址的群图片文件存储。"""
        return ImageStore(
            root=Path(config.storage.images.directory).resolve(),
            max_image_bytes=config.storage.images.max_bytes,
        )

    @provide(scope=Scope.APP)
    def get_image_archive_worker_factory(
        self,
        repository: PostgreSQLMessageRepository,
        direct_httpx: DirectHttpx,
        image_store: ImageStore,
        config: MyBotConfig,
    ) -> ImageArchiveWorkerFactory:
        """创建按 NapCat 会话绑定机器人身份的图片归档 worker 工厂。"""
        storage = config.storage.images
        return ImageArchiveWorkerFactory(
            repository=repository,
            http_client=direct_httpx,
            store=image_store,
            concurrency=storage.download_concurrency,
            download_timeout_seconds=storage.download_timeout_seconds,
            max_image_bytes=storage.max_bytes,
            lease_seconds=storage.lease_seconds,
            retry_delays_seconds=storage.retry_delays_seconds,
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
        config: MyBotConfig,
    ) -> BOTClient:
        """创建当前 WebSocket 会话的机器人客户端。"""
        return BOTClient(
            websocket=websocket,
            sent_message_recorder=repository,
            inline_image_archiver=inline_image_archiver,
            send_max_attempts=config.napcat.send_max_attempts,
            send_retry_delay_seconds=config.napcat.send_retry_delay_seconds,
        )

    @provide(scope=Scope.SESSION)
    def get_plugin_controller(
        self,
        bot: BOTClient,
        repository: PostgreSQLMessageRepository,
        repository_builder: PluginRepositoryBuilder,
        conversation_contexts: ConversationContextStore,
        config_manager: ConfigManager,
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
                bot=bot,
                group_messages=repository,
                plugin_id=cls.plugin_id,
                repository_builder=repository_builder,
                conversation_contexts=conversation_contexts,
                llm=llm,
                mcp_tool_manager=mcp_tool_manager,
                direct_httpx=directhttpx,
                proxy_httpx=proxy_httpx,
            )
            plugin_objects.append(
                cls(
                    context=context,
                    plugin_config=config_manager.bind_plugin(cls.plugin_id),
                )
            )
        return PluginController(plugin_objects=plugin_objects)

    @provide(scope=Scope.SESSION)
    def get_event_dispatcher(
        self, plugincontroller: PluginController, bot: BOTClient
    ) -> EventDispatcher:
        """创建会话事件分发器。"""
        return EventDispatcher(plugincontroller=plugincontroller, bot=bot)
