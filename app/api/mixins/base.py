"""NapCat WebSocket Action 基础能力。"""

import asyncio
import uuid
from typing import cast

from fastapi import WebSocket

from app.database import RedisDatabaseManager
from app.models.api import ActionPayload
from app.models.common import JsonObject, NapCatId, to_json_value
from app.models.events.response import Response
from app.utils.log import log_event


class BaseMixin:
    """基础 Mixin，封装 WebSocket Action 调用和响应等待。"""

    websocket: WebSocket = cast(WebSocket, cast(object, None))
    database: RedisDatabaseManager = cast(RedisDatabaseManager, cast(object, None))
    echo_dict: dict[str, asyncio.Future[Response]] = cast(
        dict[str, asyncio.Future[Response]], cast(object, None)
    )
    boot_id: NapCatId = ""
    timeout: int = 0

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
        future = self.echo_dict.get(echo)
        if future:
            future.set_result(response)

    async def create_future(self, echo: str) -> Response:
        """创建响应 Future 并等待 NapCat 回包。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Response] = loop.create_future()
        self.echo_dict[echo] = future
        try:
            _ = await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            del self.echo_dict[echo]
            log_event(
                level="ERROR",
                event="napcat.action.timeout",
                category="napcat_api",
                message="等待 NapCat 响应超时",
                echo=echo,
                timeout=self.timeout,
            )
            raise TimeoutError(f"等待 NapCat 响应超时: {echo}") from exc
        result = future.result()
        del self.echo_dict[echo]
        return result

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
