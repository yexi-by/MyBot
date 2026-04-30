"""FastAPI WebSocket 服务入口。"""

import asyncio
import copy
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from dishka import AsyncContainer
from dishka.integrations.fastapi import FromDishka, inject, setup_dishka
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from app.api import BOTClient
from app.config import Settings
from app.database import RedisDatabaseManager
from app.models import Meta, Response
from app.services import LLMHandler, MCPToolManager
from app.utils.log import log_event, log_exception, log_run_end, log_run_start

from .di import DirectHttpx, ProxyHttpx
from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker


class NapCatServer:
    """承载 NapCat 反向 WebSocket 连接的 FastAPI 服务。"""

    def __init__(self, container: AsyncContainer, settings: Settings) -> None:
        """创建 FastAPI 应用并注册路由。"""
        self.container: AsyncContainer = container
        self.settings: Settings = settings
        self.app: FastAPI = FastAPI(lifespan=self.lifespan)
        setup_dishka(self.container, self.app)
        self._register_routes()
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _track_background_task(self, task: asyncio.Task[None]) -> None:
        """持有后台事件分发任务引用，并在失败时记录异常。"""
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(self._log_background_task_result)

    def _log_background_task_result(self, task: asyncio.Task[None]) -> None:
        """记录后台事件分发任务的异常结果。"""
        if task.cancelled():
            log_event(
                level="DEBUG",
                event="event_dispatch.cancelled",
                category="dispatcher",
                message="事件分发任务已取消",
            )
            return
        exc = task.exception()
        if exc is None:
            return
        log_exception(
            event="event_dispatch.exception",
            category="dispatcher",
            message="事件分发任务失败",
            exc=exc,
        )

    @asynccontextmanager
    async def lifespan(self, _app: FastAPI) -> AsyncIterator[None]:
        """管理应用启动预热与关闭清理。"""
        log_run_start(
            message="正在初始化服务组件",
            app_name=self.settings.app.name,
            environment=self.settings.app.environment,
            host=self.settings.server.host,
            port=self.settings.server.port,
            websocket_path_prefix=self.settings.server.websocket_path_prefix,
        )
        # Dishka 按需创建依赖；启动阶段先解析全局单例，保证后续会话级依赖可以直接复用。
        _ = await self.container.get(RedisDatabaseManager)
        mcp_tool_manager = await self.container.get(MCPToolManager)
        await mcp_tool_manager.start()
        # 预热网络客户端和 LLM 路由，启动日志能覆盖关键运行依赖。
        _ = await asyncio.gather(
            self.container.get(DirectHttpx),
            self.container.get(ProxyHttpx | None),
            self.container.get(LLMHandler | None),
        )
        log_event(
            level="SUCCESS",
            event="app.startup.ready",
            category="runtime",
            message="服务启动完成，等待客户端连接",
        )
        yield
        log_event(
            level="INFO",
            event="app.shutdown.start",
            category="runtime",
            message="正在关闭服务",
        )
        mcp_tool_manager = await self.container.get(MCPToolManager)
        await mcp_tool_manager.close()
        redis_database_manager = await self.container.get(RedisDatabaseManager)
        await redis_database_manager.stop_consumers()
        await self.container.close()
        log_run_end(message="服务已安全关闭")

    async def _check_auth_token(
        self,
        websocket: WebSocket,
    ) -> None:
        """校验 NapCat WebSocket Bearer Token。"""
        setting = await self.container.get(Settings)
        token = setting.napcat.websocket_token
        headers = websocket.headers
        auth_header = headers.get("authorization", "")
        expected_header = "Bearer " + token
        if not secrets.compare_digest(auth_header, expected_header):  # 减少 Token 比较侧信道。
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            raise ValueError("NapCat WebSocket Token 校验失败")
        await websocket.accept()

    def _register_routes(self) -> None:
        """注册 WebSocket 路由。"""

        websocket_path = f"{self.settings.server.websocket_path_prefix}/{{client_id}}"

        @self.app.websocket(websocket_path)
        @inject
        async def websocket_endpoint(
            websocket: WebSocket,
            client_id: str,
            checker: FromDishka[EventTypeChecker],
            redis_database_manager: FromDishka[RedisDatabaseManager],
        ) -> None:
            """处理单个 NapCat WebSocket 客户端连接。"""
            async with self.container(
                context={WebSocket: websocket}
            ) as request_container:
                try:
                    await self._check_auth_token(websocket=websocket)
                except ValueError:
                    return
                dispatcher = await request_container.get(EventDispatcher)
                bot = await request_container.get(BOTClient)
                try:
                    while True:
                        data_str = await websocket.receive_text()
                        raw_data = cast(object, json.loads(data_str))
                        if not isinstance(raw_data, dict):
                            log_event(
                                level="WARNING",
                                event="websocket.event.invalid_payload",
                                category="websocket",
                                message="收到非对象格式事件，已跳过",
                                client_id=client_id,
                            )
                            continue
                        data = cast(dict[str, object], raw_data)
                        event = checker.get_event(data)
                        if event is None:
                            continue
                        if not isinstance(event, (Meta, Response)):
                            log_event(
                                level="DEBUG",
                                event="websocket.event.received",
                                category="websocket",
                                message="收到 NapCat 事件",
                                client_id=client_id,
                                event_type=event.post_type,
                                event_model=type(event).__name__,
                            )
                        bot.get_self_qq_id(msg=event)
                        # 分发侧允许插件读取和加工事件副本，入库侧记录 NapCat 原始事件语义。
                        copy_event = copy.deepcopy(event)
                        task = asyncio.create_task(
                            dispatcher.dispatch_event(event=copy_event)
                        )
                        self._track_background_task(task=task)
                        if isinstance(event, Response):
                            await bot.receive_data(response=event)
                            continue
                        await redis_database_manager.add_to_queue(event)
                except WebSocketDisconnect as e:
                    log_event(
                        level="INFO",
                        event="websocket.client.disconnected",
                        category="websocket",
                        message="客户端断开连接",
                        client_id=client_id,
                        reason=str(e),
                    )
                except RuntimeError as e:
                    log_event(
                        level="WARNING",
                        event="websocket.client.runtime_closed",
                        category="websocket",
                        message="客户端连接异常断开",
                        client_id=client_id,
                        reason=str(e),
                    )
                except Exception as e:
                    log_exception(
                        event="websocket.client.exception",
                        category="websocket",
                        message="客户端处理异常",
                        exc=e,
                        client_id=client_id,
                    )
                    if websocket.client_state.name == "CONNECTED":
                        try:
                            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                        except RuntimeError as close_error:
                            log_event(
                                level="DEBUG",
                                event="websocket.client.close_failed",
                                category="websocket",
                                message="异常后关闭客户端连接失败",
                                client_id=client_id,
                                reason=str(close_error),
                            )
                finally:
                    log_event(
                        level="INFO",
                        event="websocket.client.cleanup_start",
                        category="websocket",
                        message="正在清理客户端资源",
                        client_id=client_id,
                    )
                    shutdown_tasks = [
                        plugin.stop_consumers()
                        for plugin in dispatcher.plugincontroller.plugin_objects
                    ]
                    if shutdown_tasks:
                        _ = await asyncio.gather(*shutdown_tasks)
                    log_event(
                        level="SUCCESS",
                        event="websocket.client.cleanup_done",
                        category="websocket",
                        message="客户端资源清理完成",
                        client_id=client_id,
                    )

        _ = websocket_endpoint
