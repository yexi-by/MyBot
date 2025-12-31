import asyncio
import json
import secrets
from contextlib import asynccontextmanager

from dishka import AsyncContainer
from dishka.integrations.fastapi import inject, setup_dishka
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from utils import logger

from app.plugins import ACTIVE_INSTANCES
from app.utils import write_to_file

from .dispatcher import EventDispatcher


class NapCatServer:
    def __init__(self, container: AsyncContainer) -> None:
        self.app = FastAPI()
        self.container = container
        setup_dishka(self.container, self.app)
        self._register_routes()

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        logger.info("服务已启动")
        yield
        await self.container.close()
        shutdown_tasks = []
        for plugin in ACTIVE_INSTANCES:
            logger.info(f"等待插件 {plugin.name} 队列完成...")
            shutdown_tasks.append(plugin.task_queue.join())
        if shutdown_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*shutdown_tasks), timeout=10.0)
                logger.info("所有插件队列均为空")
            except asyncio.TimeoutError:
                logger.error("插件队列超时")
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
        ) -> None:
            try:
                await self._check_auth_token(websocket=websocket)
            except ValueError:
                return
            async with self.container(
                context={WebSocket: websocket}
            ) as request_container:
                dispatcher = await request_container.get(EventDispatcher)
                try:
                    while True:
                        data_str = await websocket.receive_text()
                        data = json.loads(data_str)
                        await write_to_file(data=data)
                        asyncio.create_task(dispatcher.dispatch_event(data=data))
                except WebSocketDisconnect as e:
                    logger.error(e)
                except Exception as e:
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                    logger.error(e)
