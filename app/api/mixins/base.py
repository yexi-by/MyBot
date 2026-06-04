"""NapCat WebSocket Action 基础能力。"""

import asyncio
import uuid
from typing import cast

from fastapi import WebSocket

from app.database import RedisDatabaseManager
from app.models.api import ActionPayload
from app.models.common import JsonObject, NapCatId, to_json_value
from app.models.events.response import Response, StreamTransferResult
from app.utils.log import log_event


class BaseMixin:
    """基础 Mixin，封装 WebSocket Action 调用和响应等待。"""

    websocket: WebSocket = cast(WebSocket, cast(object, None))
    database: RedisDatabaseManager = cast(RedisDatabaseManager, cast(object, None))
    echo_dict: dict[str, asyncio.Future[Response]] = cast(
        dict[str, asyncio.Future[Response]], cast(object, None)
    )
    stream_dict: dict[str, asyncio.Queue[Response]] = cast(
        dict[str, asyncio.Queue[Response]], cast(object, None)
    )
    boot_id: NapCatId = ""
    timeout: int = 0
    send_retry_count: int = 3
    send_retry_delay: int = 1

    def _generate_echo(self) -> str:
        """生成唯一 echo 标识。"""
        return str(uuid.uuid4())

    def _build_params(self, **items: object) -> JsonObject:
        """构造 NapCat 参数对象，过滤未传入的可选字段。"""
        params: JsonObject = {}
        for key, value in items.items():
            if value is None:
                continue
            params[key] = to_json_value(value)
        return params

    async def receive_data(self, response: Response) -> None:
        """将 Action 响应回填到等待中的 Future。"""
        echo = response.echo
        if not echo:
            return
        stream_queue = self.stream_dict.get(echo)
        if stream_queue is not None:
            await stream_queue.put(response)
            return
        future = self.echo_dict.get(echo)
        if future and not future.done():
            future.set_result(response)

    async def create_future(self, echo: str) -> Response:
        """创建响应 Future 并等待 NapCat 回包。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Response] = loop.create_future()
        self.echo_dict[echo] = future
        try:
            _ = await asyncio.wait_for(future, timeout=self.timeout)
            return future.result()
        except asyncio.TimeoutError as exc:
            log_event(
                level="ERROR",
                event="napcat.action.timeout",
                category="napcat_api",
                message="等待 NapCat 响应超时",
                echo=echo,
                timeout=self.timeout,
            )
            raise TimeoutError(f"等待 NapCat 响应超时: {echo}") from exc
        finally:
            self.echo_dict.pop(echo, None)

    def _is_stream_transfer_done(self, response: Response) -> bool:
        """判断 Stream Action 是否已经抵达最终响应包。"""
        if response.stream != "stream-action":
            return True
        data = response.data
        if not isinstance(data, dict):
            return False
        packet_type = data.get("type")
        return packet_type in {"response", "error"}

    async def create_stream_transfer(
        self, echo: str, queue: asyncio.Queue[Response]
    ) -> StreamTransferResult:
        """持续收集同一 echo 的 Stream Action 回包。"""
        packets: list[Response] = []
        try:
            while True:
                response = await asyncio.wait_for(queue.get(), timeout=self.timeout)
                packets.append(response)
                if self._is_stream_transfer_done(response):
                    return StreamTransferResult(
                        packets=packets, final_response=response
                    )
        except asyncio.TimeoutError as exc:
            log_event(
                level="ERROR",
                event="napcat.stream_action.timeout",
                category="napcat_api",
                message="等待 NapCat Stream 响应超时",
                echo=echo,
                timeout=self.timeout,
            )
            raise TimeoutError(f"等待 NapCat Stream 响应超时: {echo}") from exc
        finally:
            self.stream_dict.pop(echo, None)

    async def _send_action(
        self, action: str, params: JsonObject | None = None
    ) -> None:
        """发送不需要等待回包的 NapCat Action。"""
        payload = ActionPayload(action=action, params=params)
        await self.websocket.send_text(payload.model_dump_json(exclude_none=True))

    async def _call_action(
        self, action: str, params: JsonObject | None = None
    ) -> Response:
        """发送需要等待回包的 NapCat Action。"""
        echo = self._generate_echo()
        payload = ActionPayload(action=action, params=params, echo=echo)
        await self.websocket.send_text(payload.model_dump_json(exclude_none=True))
        return await self.create_future(echo=echo)

    async def _call_stream_action(
        self, action: str, params: JsonObject | None = None
    ) -> StreamTransferResult:
        """发送 Stream Action 并收集同一 echo 下的完整回包序列。"""
        echo = self._generate_echo()
        queue: asyncio.Queue[Response] = asyncio.Queue()
        self.stream_dict[echo] = queue
        payload = ActionPayload(action=action, params=params, echo=echo)
        try:
            await self.websocket.send_text(payload.model_dump_json(exclude_none=True))
        except Exception:
            self.stream_dict.pop(echo, None)
            raise
        return await self.create_stream_transfer(echo=echo, queue=queue)
