"""FastAPI WebSocket 服务入口。"""

import asyncio
import copy
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

from dishka import AsyncContainer
from dishka.integrations.fastapi import FromDishka, inject, setup_dishka
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from app.api import BOTClient
from app.config import Settings
from app.database import (
    DatabaseMigrator,
    GroupDataScope,
    PostgreSQLMessageRepository,
    PostgreSQLRuntime,
)
from app.models import GroupMessage, GroupRecallNoticeEvent, Meta, Response
from app.services import LLMHandler, MCPToolManager
from app.services.napcat import ImageArchiveWorkerFactory
from app.utils.log import log_event, log_exception, log_run_end, log_run_start

from .di import DirectHttpx, ProxyHttpx
from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker

_PERSISTENCE_RETRY_DELAY_SECONDS = 0.25
_IMAGE_WORKER_STOP_TIMEOUT_SECONDS = 5.0


class EventPersistenceError(RuntimeError):
    """事件在两次 PostgreSQL 写入后仍无法持久化。"""


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
        """检查数据库版本并管理全局网络与连接池资源。"""
        log_run_start(
            message="正在初始化服务组件",
            app_name=self.settings.app.name,
            environment=self.settings.app.environment,
            host=self.settings.server.host,
            port=self.settings.server.port,
            websocket_path_prefix=self.settings.server.websocket_path_prefix,
        )
        runtime: PostgreSQLRuntime | None = None
        mcp_tool_manager: MCPToolManager | None = None
        direct_httpx: DirectHttpx | None = None
        proxy_httpx: ProxyHttpx | None = None
        active_error: BaseException | None = None
        try:
            runtime = await self.container.get(PostgreSQLRuntime)
            migrator = await self.container.get(DatabaseMigrator)
            mcp_tool_manager = await self.container.get(MCPToolManager)
            direct_httpx = await self.container.get(DirectHttpx)
            proxy_httpx = await self.container.get(ProxyHttpx | None)
            await runtime.check_connection()
            await migrator.assert_current()
            await mcp_tool_manager.start()
            _ = await self.container.get(PostgreSQLMessageRepository)
            _ = await self.container.get(ImageArchiveWorkerFactory)
            _ = await self.container.get(LLMHandler | None)
            log_event(
                level="SUCCESS",
                event="app.startup.ready",
                category="runtime",
                message="PostgreSQL 连接和 migration 版本检查通过，等待客户端连接",
            )
            yield
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            log_event(
                level="INFO",
                event="app.shutdown.start",
                category="runtime",
                message="正在关闭服务",
            )
            shutdown_errors: list[BaseException] = []

            async def close_resource(
                *,
                resource_name: str,
                operation: Callable[[], Awaitable[None]],
            ) -> None:
                """单项关闭失败时记录错误，并继续释放其余资源。"""
                try:
                    await operation()
                except BaseException as exc:
                    shutdown_errors.append(exc)
                    log_exception(
                        event="app.shutdown.resource_failed",
                        category="runtime",
                        message="关闭服务资源失败，正在继续释放其余资源",
                        exc=exc,
                        resource_name=resource_name,
                    )

            if mcp_tool_manager is not None:
                await close_resource(
                    resource_name="mcp_tool_manager",
                    operation=mcp_tool_manager.close,
                )
            if direct_httpx is not None:
                await close_resource(
                    resource_name="direct_httpx",
                    operation=direct_httpx.aclose,
                )
            if proxy_httpx is not None:
                await close_resource(
                    resource_name="proxy_httpx",
                    operation=proxy_httpx.aclose,
                )
            if runtime is not None:
                await close_resource(
                    resource_name="postgresql_runtime",
                    operation=runtime.dispose,
                )
            await close_resource(
                resource_name="dishka_container",
                operation=self.container.close,
            )
            if shutdown_errors:
                log_run_end(message="服务已关闭，但部分资源关闭失败")
                if active_error is None:
                    raise BaseExceptionGroup("服务资源关闭失败", shutdown_errors)
            else:
                log_run_end(message="服务已安全关闭")

    async def _check_auth_token(self, websocket: WebSocket) -> None:
        """校验 NapCat WebSocket Bearer Token。"""
        setting = await self.container.get(Settings)
        token = setting.napcat.websocket_token
        auth_header = websocket.headers.get("authorization", "")
        expected_header = "Bearer " + token
        if not secrets.compare_digest(auth_header, expected_header):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            raise ValueError("NapCat WebSocket Token 校验失败")
        await websocket.accept()

    async def _persist_with_retry[
        ResultT
    ](
        self,
        *,
        operation: Callable[[], Awaitable[ResultT]],
        event_name: str,
        event_model: str,
        message_id: str,
    ) -> ResultT:
        """PostgreSQL 写入失败后等待 250ms，并且只重试一次。"""
        first_error: Exception | None = None
        for attempt_number in (1, 2):
            try:
                return await operation()
            except Exception as exc:
                if attempt_number == 1:
                    first_error = exc
                    log_event(
                        level="WARNING",
                        event=f"{event_name}.retry",
                        category="database",
                        message="PostgreSQL 写入失败，250ms 后重试一次",
                        event_model=event_model,
                        message_id=message_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    await asyncio.sleep(_PERSISTENCE_RETRY_DELAY_SECONDS)
                    continue
                log_exception(
                    event=f"{event_name}.failed",
                    category="database",
                    message="PostgreSQL 写入连续失败，当前 NapCat 会话必须终止",
                    exc=exc,
                    event_model=event_model,
                    message_id=message_id,
                    first_error_type=(
                        type(first_error).__name__
                        if first_error is not None
                        else None
                    ),
                )
                log_event(
                    level="CRITICAL",
                    event=f"{event_name}.session_aborted",
                    category="database",
                    message="事件未分发，正在以 1011 终止 NapCat 会话",
                    event_model=event_model,
                    message_id=message_id,
                )
                raise EventPersistenceError(
                    f"{event_model} 持久化连续失败"
                ) from exc
        raise AssertionError("持久化重试循环异常结束")

    async def _persist_event(
        self,
        *,
        event: GroupMessage | GroupRecallNoticeEvent,
        repository: PostgreSQLMessageRepository,
    ) -> None:
        """在插件分发前持久化群消息或撤回归档。"""
        if isinstance(event, GroupMessage):
            async def save_message() -> None:
                await repository.save_incoming(event)

            _ = await self._persist_with_retry(
                operation=save_message,
                event_name="database.group_message.persist",
                event_model=type(event).__name__,
                message_id=str(event.message_id),
            )
            return

        scope = GroupDataScope(
            bot_id=str(event.self_id),
            group_id=str(event.group_id),
        )

        async def archive_message() -> bool:
            return await repository.archive(
                scope=scope,
                message_id=str(event.message_id),
                recalled_at=datetime.fromtimestamp(event.time, tz=UTC),
                recalled_by_id=str(event.operator_id),
            )

        archived = await self._persist_with_retry(
            operation=archive_message,
            event_name="database.group_message.archive",
            event_model=type(event).__name__,
            message_id=str(event.message_id),
        )
        if not archived:
            log_event(
                level="WARNING",
                event="database.group_message.archive_target_missing",
                category="database",
                message="撤回目标不在当前空库历史中，未创建额外通知记录",
                bot_id=scope.bot_id,
                group_id=scope.group_id,
                message_id=str(event.message_id),
                recalled_by_id=str(event.operator_id),
            )

    async def _stop_image_worker(
        self,
        *,
        stop_event: asyncio.Event | None,
        worker_task: asyncio.Task[None] | None,
        client_id: str,
    ) -> None:
        """停止当前机器人图片 worker；超时任务由数据库租约恢复。"""
        if stop_event is None or worker_task is None:
            return
        stop_event.set()
        try:
            await asyncio.wait_for(
                asyncio.shield(worker_task),
                timeout=_IMAGE_WORKER_STOP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            worker_task.cancel()
            _ = await asyncio.gather(worker_task, return_exceptions=True)
            log_event(
                level="WARNING",
                event="napcat.image_archive.stop_timeout",
                category="napcat_tools",
                message="图片归档 worker 停止超时，未完成任务将由租约恢复",
                client_id=client_id,
            )

    def _register_routes(self) -> None:
        """注册 WebSocket 路由。"""
        websocket_path = f"{self.settings.server.websocket_path_prefix}/{{client_id}}"

        @self.app.websocket(websocket_path)
        @inject
        async def websocket_endpoint(
            websocket: WebSocket,
            client_id: str,
            checker: FromDishka[EventTypeChecker],
            repository: FromDishka[PostgreSQLMessageRepository],
            image_worker_factory: FromDishka[ImageArchiveWorkerFactory],
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
                image_worker_stop: asyncio.Event | None = None
                image_worker_task: asyncio.Task[None] | None = None
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
                        event = checker.get_event(cast(dict[str, object], raw_data))
                        if event is None:
                            continue
                        if isinstance(event, Response):
                            await bot.receive_data(response=event)
                            continue
                        if not isinstance(event, Meta):
                            log_event(
                                level="DEBUG",
                                event="websocket.event.received",
                                category="websocket",
                                message="收到 NapCat 事件",
                                client_id=client_id,
                                event_type=event.post_type,
                                event_model=type(event).__name__,
                            )
                        if (
                            bot.boot_id != ""
                            and str(bot.boot_id) != str(event.self_id)
                        ):
                            log_event(
                                level="CRITICAL",
                                event="websocket.bot_identity.changed",
                                category="websocket",
                                message="同一 NapCat 会话出现不同机器人身份，已拒绝继续处理",
                                client_id=client_id,
                                expected_bot_id=str(bot.boot_id),
                                actual_bot_id=str(event.self_id),
                            )
                            await websocket.close(
                                code=status.WS_1008_POLICY_VIOLATION,
                                reason="NapCat 机器人身份发生变化",
                            )
                            break
                        bot.get_self_qq_id(msg=event)
                        if image_worker_task is None:
                            image_worker_stop = asyncio.Event()
                            image_worker = image_worker_factory.create(
                                bot_id=str(event.self_id),
                                bot=bot,
                            )
                            image_worker_task = asyncio.create_task(
                                image_worker.run(stop_event=image_worker_stop)
                            )
                        if isinstance(event, (GroupMessage, GroupRecallNoticeEvent)):
                            await self._persist_event(
                                event=event,
                                repository=repository,
                            )
                        task = asyncio.create_task(
                            dispatcher.dispatch_event(event=copy.deepcopy(event))
                        )
                        self._track_background_task(task=task)
                except EventPersistenceError:
                    if websocket.client_state.name == "CONNECTED":
                        try:
                            await websocket.close(
                                code=status.WS_1011_INTERNAL_ERROR,
                                reason="PostgreSQL 持久化失败",
                            )
                        except RuntimeError:
                            pass
                except WebSocketDisconnect as exc:
                    log_event(
                        level="INFO",
                        event="websocket.client.disconnected",
                        category="websocket",
                        message="客户端断开连接",
                        client_id=client_id,
                        reason=str(exc),
                    )
                except RuntimeError as exc:
                    log_event(
                        level="WARNING",
                        event="websocket.client.runtime_closed",
                        category="websocket",
                        message="客户端连接异常断开",
                        client_id=client_id,
                        reason=str(exc),
                    )
                except Exception as exc:
                    log_exception(
                        event="websocket.client.exception",
                        category="websocket",
                        message="客户端处理异常",
                        exc=exc,
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
                    await self._stop_image_worker(
                        stop_event=image_worker_stop,
                        worker_task=image_worker_task,
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
