import asyncio
import json
import secrets
from contextlib import asynccontextmanager

from dishka import AsyncContainer
from dishka.integrations.fastapi import FromDishka, inject, setup_dishka
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from app.api import BOTClient
from app.database import RedisDatabaseManager
from app.models import Response
from app.utils import logger, write_to_file

from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker


class NapCatServer:
    def __init__(self, container: AsyncContainer) -> None:
        self.app = FastAPI()
        self.container = container
        setup_dishka(self.container, self.app)
        self._register_routes()
        self._background_tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        logger.info("服务已启动")
        yield
        redis_database_manager = await self.container.get(RedisDatabaseManager)
        await redis_database_manager.stop_consumers()
        await self.container.close()
        logger.info("服务器关闭完成")

    async def _check_auth_token(
        self, websocket: WebSocket, token: str = "adm12345"
    ) -> None:
        headers = websocket.headers
        auth_header = headers.get("authorization", "")
        expected_header = "Bearer " + token
        if not secrets.compare_digest(auth_header, expected_header):  # 防时序攻击
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            raise ValueError
        await websocket.accept()

    def _register_routes(self):
        @self.app.websocket("/ws/{client_id}")
        @inject
        async def websocket_endpoint(
            websocket: WebSocket,
            client_id: str,
            checker: FromDishka[EventTypeChecker],
            redis_database_manager: FromDishka[RedisDatabaseManager],
        ) -> None:
            try:
                await self._check_auth_token(websocket=websocket)
            except ValueError:
                return
            async with self.container(
                context={WebSocket: websocket}
            ) as request_container:
                dispatcher = await request_container.get(EventDispatcher)
                bot = await request_container.get(BOTClient)
                try:
                    while True:
                        data_str = await websocket.receive_text()
                        data = json.loads(data_str)
                        logger.debug(f"收到数据包: {data}")
                        await write_to_file(data=data)
                        event = checker.get_event(data)
                        if event is None:
                            continue
                        task = asyncio.create_task(
                            dispatcher.dispatch_event(event=event)
                        )
                        self._background_tasks.add(
                            task
                        )  # 持有asyncio.Task强引用，防止在某些情况被gc回收
                        task.add_done_callback(
                            self._background_tasks.discard
                        )  # 做完自动删除 防内存泄漏
                        if isinstance(event, Response):
                            bot.receive_data(response=event)
                            continue
                        await redis_database_manager.add_to_queue(event)
                except WebSocketDisconnect as e:
                    logger.info(f"WebSocket 连接断开: {e}")
                except RuntimeError as e:
                    # 处理 WebSocket 未连接或已关闭的情况
                    logger.warning(f"WebSocket 运行时错误（连接可能已断开）: {e}")
                except Exception as e:
                    logger.error(f"WebSocket 处理异常: {e}")
                    # 只有在连接仍然打开时才尝试关闭
                    if websocket.client_state.name == "CONNECTED":
                        try:
                            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                        except RuntimeError:
                            pass  # 连接已关闭，忽略
                finally:
                    logger.info(f"正在清理会话 {client_id} 的插件资源...")
                    shutdown_tasks = []
                    for plugin in dispatcher.plugincontroller.plugin_objects:
                        logger.info(f"等待插件 {plugin.name} 队列完成...")
                        shutdown_tasks.append(plugin.stop_consumers())
                    if shutdown_tasks:
                        await asyncio.gather(*shutdown_tasks)
                    logger.info(f"会话 {client_id} 清理完成")
