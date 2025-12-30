from typing import NewType

import httpx
from dishka import Provider, Scope, from_context, provide
from fastapi import WebSocket

from app.api import BOTClient
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding
from config import RAW_CONFIG_DICT, Settings

from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker
from .plugin_manager import PluginController

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
    def get_llm_handler(self, settings: Settings) -> LLMHandler:
        return LLMHandler.register_instance(settings.llm_settings)

    @provide(scope=Scope.APP)
    def get_siliconflow_embedding(
        self, settings: Settings, client: DirectHttpx
    ) -> SiliconFlowEmbedding:
        return SiliconFlowEmbedding(
            embedding_config=settings.embedding_settings,
            client=client,
        )

    @provide(scope=Scope.APP)
    async def get_search_vectors(self, settings: Settings) -> SearchVectors:
        return await SearchVectors.create_from_directory(
            directory=settings.faiss_file_location
        )

    @provide(scope=Scope.APP)
    def get_event_type_checker(self) -> EventTypeChecker:
        return EventTypeChecker()

    @provide(scope=Scope.SESSION)
    def get_bot_client(self, websocket: WebSocket) -> BOTClient:
        return BOTClient(websocket=websocket)

    @provide(scope=Scope.SESSION)
    def get_plugin_controller(
        self,
        llm: LLMHandler,
        siliconflow: SiliconFlowEmbedding,
        search_vectors: SearchVectors,
        bot: BOTClient,
    ) -> PluginController:
        return PluginController(
            llm=llm,
            siliconflow=siliconflow,
            search_vectors=search_vectors,
            bot=bot,
        )

    @provide(scope=Scope.SESSION)
    def get_event_dispatcher(
        self,
        checker: EventTypeChecker,
        plugincontroller: PluginController,
    ) -> EventDispatcher:
        return EventDispatcher(
            checker=checker,
            plugincontroller=plugincontroller,
        )
