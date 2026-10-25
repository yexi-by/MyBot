"""NapCat WebSocket Action 基础能力。"""

import asyncio
import uuid
from typing import cast

from fastapi import WebSocket

from app.database import SentMessageRecorder
from app.models.api import ActionPayload
from app.models.common import JsonObject, NapCatId, to_json_value
from app.models.events.response import Response
from app.services.napcat import InlineImageArchiver
from app.utils.log import log_event


class BaseMixin:
    """基础 Mixin，封装 WebSocket Action 调用和响应等待。"""

    websocket: WebSocket = cast(WebSocket, cast(object, None))
    sent_message_recorder: SentMessageRecorder = cast(
        SentMessageRecorder, cast(object, None)
    )
    inline_image_archiver: InlineImageArchiver = cast(
        InlineImageArchiver, cast(object, None)
    )
    echo_dict: dict[str, asyncio.Future[Response]] = cast(
        dict[str, asyncio.Future[Response]], cast(object, None)
    )
    persistence_failed_event: asyncio.Event = cast(
        asyncio.Event, cast(object, None)
    )
    boot_id: NapCatId = ""
    timeout: float
    send_max_attempts: int
    send_retry_delay_seconds: float
    send_retry_max_delay_seconds: float
    response_summary_max_chars: int
    persistence_retry_delays_seconds: tuple[float, ...]

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
        if future and not future.done():
            future.set_result(response)

    async def _send_action(
        self, action: str, params: JsonObject | None = None
    ) -> None:
        """发送不需要等待回包的 NapCat Action。"""
        self._ensure_persistence_healthy()
        payload = ActionPayload(action=action, params=params)
        await self.websocket.send_text(payload.model_dump_json(exclude_none=True))

    async def _close_for_persistence_failure(self) -> None:
        """出站消息已发送但无法记录时终止当前 NapCat 会话。"""
        self.persistence_failed_event.set()
        try:
            await self.websocket.close(code=1011, reason="PostgreSQL 持久化失败")
        except RuntimeError as exc:
            log_event(
                level="DEBUG",
                event="napcat.persistence_close.skipped",
                category="napcat_api",
                message="数据库失败后 WebSocket 已经关闭",
                reason=str(exc),
            )

    def _ensure_persistence_healthy(self) -> None:
        """数据库失败后的会话不得继续发起 NapCat Action。"""
        if self.persistence_failed_event.is_set():
            raise RuntimeError("当前 NapCat 会话因 PostgreSQL 持久化失败已停止")

    async def _call_action(
        self, action: str, params: JsonObject | None = None
    ) -> Response:
        """发送需要等待回包的 NapCat Action。"""
        self._ensure_persistence_healthy()
        echo = self._generate_echo()
        payload = ActionPayload(action=action, params=params, echo=echo)
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self.echo_dict[echo] = future
        try:
            await self.websocket.send_text(payload.model_dump_json(exclude_none=True))
            return await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError as exc:
            log_event(
                level="ERROR",
                event="napcat.action.timeout",
                category="napcat_api",
                message="等待 NapCat 响应超时",
                action=action,
                echo=echo,
                timeout=self.timeout,
            )
            raise TimeoutError(f"等待 NapCat 响应超时: {echo}") from exc
        finally:
            self.echo_dict.pop(echo, None)
            if not future.done():
                _ = future.cancel()
