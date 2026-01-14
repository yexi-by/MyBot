import asyncio
import json
import secrets
from contextlib import asynccontextmanager
import copy
from dishka import AsyncContainer
from dishka.integrations.fastapi import FromDishka, inject, setup_dishka
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from app.api import BOTClient
from app.database import RedisDatabaseManager
from app.models import Response, Meta
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding
from app.services.ai_image import NaiClient
from app.utils import logger
from app.config import Settings
from .di import DirectHttpx, ProxyHttpx
from .dispatcher import EventDispatcher
from .event_parser import EventTypeChecker


class NapCatServer:
    def __init__(self, container: AsyncContainer) -> None:
        self.container = container
        self.app = FastAPI(lifespan=self.lifespan)
        setup_dishka(self.container, self.app)
        self._register_routes()
        self._background_tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        logger.info("正在初始化服务组件...")
        # 因为dishka是惰性加载的,所以在首次运行的时候需要先把全局对象给预热提前加载
        # 否则大概率发生*会话级*实例创建的时候缺少全局依赖导致创建失败的现象
        await self.container.get(RedisDatabaseManager)
        # 并发预热组件
        await asyncio.gather(
            self.container.get(DirectHttpx),
            self.container.get(ProxyHttpx | None),
            self.container.get(LLMHandler | None),
            self.container.get(SiliconFlowEmbedding | None),
            self.container.get(SearchVectors | None),
            self.container.get(NaiClient | None),
        )
        logger.info("服务启动完成，等待客户端连接")
        yield
        logger.info("正在关闭服务...")
        redis_database_manager = await self.container.get(RedisDatabaseManager)
        await redis_database_manager.stop_consumers()
        await self.container.close()
        logger.info("redis数据库服务已关闭")
        logger.info("服务已安全关闭")

    async def _check_auth_token(
        self,
        websocket: WebSocket,
    ) -> None:
        setting = await self.container.get(Settings)
        password = setting.password
        headers = websocket.headers
        auth_header = headers.get("authorization", "")
        expected_header = "Bearer " + password
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
                        data = json.loads(data_str)
                        event = checker.get_event(data)
                        if event is None:
                            continue
                        if not isinstance(event, Meta):
                            logger.info(event.model_dump_json(indent=2))
                        bot.get_self_qq_id(msg=event)
                        # 因为dispatch_event和add_to_queue对event进行并发处理可能会有不可观测的逻辑错误和竞争行为,
                        # 这里对event进行深拷贝,防止隐性bug
                        copy_event = copy.deepcopy(event)
                        task = asyncio.create_task(
                            dispatcher.dispatch_event(event=copy_event)
                        )
                        self._background_tasks.add(
                            task
                        )  # 持有asyncio.Task强引用，防止在某些情况被gc回收
                        task.add_done_callback(
                            self._background_tasks.discard
                        )  # 做完自动删除 防内存泄漏
                        if isinstance(event, Response):
                            await bot.receive_data(response=event)
                            continue
                        await redis_database_manager.add_to_queue(event)
                except WebSocketDisconnect as e:
                    logger.info(f"客户端 {client_id} 断开连接: {e}")
                except RuntimeError as e:
                    logger.warning(f"客户端 {client_id} 连接异常断开: {e}")
                except Exception as e:
                    logger.exception(f"客户端 {client_id} 处理异常: {e}")
                    if websocket.client_state.name == "CONNECTED":
                        try:
                            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                        except RuntimeError:
                            pass
                finally:
                    logger.info(f"正在清理客户端 {client_id} 的资源...")
                    shutdown_tasks = [
                        plugin.stop_consumers()
                        for plugin in dispatcher.plugincontroller.plugin_objects
                    ]
                    if shutdown_tasks:
                        await asyncio.gather(*shutdown_tasks)
                    logger.info(f"客户端 {client_id} 资源清理完成")
