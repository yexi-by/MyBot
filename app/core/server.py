import json
import secrets

from dishka import AsyncContainer
from dishka.integrations.fastapi import inject, setup_dishka
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from app.utils import write_to_file

from .dispatcher import EventDispatcher


class NapCatServer:
    def __init__(self, container: AsyncContainer) -> None:
        self.app = FastAPI()
        self.container = container
        setup_dishka(self.container, self.app)
        self._register_routes()

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
                    await dispatcher.dispatch_event(data=data)

            except WebSocketDisconnect:
                pass
            except Exception:
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                pass
            except Exception:
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                pass
            except Exception:
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
