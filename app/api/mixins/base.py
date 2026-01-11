"""基础 Mixin 类

提供 BOTClient 的基础功能，包括 WebSocket 通信和 Future 管理。
"""

import asyncio
import uuid
from typing import Protocol, runtime_checkable

from fastapi import WebSocket

from app.database import RedisDatabaseManager
from app.models.events.response import (
    Response,
)
from app.utils import logger


@runtime_checkable
class SupportsModelDumpJson(Protocol):
    """支持 model_dump_json 方法的协议（Pydantic BaseModel 兼容）"""

    def model_dump_json(self) -> str: ...


@runtime_checkable
class SupportsEcho(Protocol):
    """带有 echo 字段的协议"""

    echo: str


@runtime_checkable
class PayloadWithEchoProtocol(SupportsModelDumpJson, SupportsEcho, Protocol):
    """带有 echo 字段且支持序列化的 Payload 协议

    用于类型标注需要 echo 字段的 Payload 类型，
    如 PrivateMessagePayload, GroupMessagePayload 等。
    """

    pass


# 使用 type 别名定义 Payload 类型
type Payload = SupportsModelDumpJson  # 任何支持 model_dump_json 的对象
type PayloadWithEcho = PayloadWithEchoProtocol  # 带有 echo 字段且支持序列化的 Payload


class BaseMixin:
    """基础 Mixin，提供 WebSocket 通信基础设施"""

    websocket: WebSocket
    database: RedisDatabaseManager
    echo_dict: dict[str, asyncio.Future[Response]]
    boot_id: int
    timeout: int

    def _generate_echo(self) -> str:
        """生成唯一的 echo 标识"""
        return str(uuid.uuid4())

    async def receive_data(self, response: Response) -> None:
        """对外接口,找到对应future并填充"""
        echo = response.echo
        if not echo:
            return
        future = self.echo_dict.get(echo)
        if future:
            future.set_result(response)

    async def create_future(self, echo: str) -> Response:
        """创建future 监听future完成"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Response] = loop.create_future()
        self.echo_dict[echo] = future
        try:
            await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            del self.echo_dict[echo]
            logger.error(f"等待响应超时 (echo={echo}, timeout={self.timeout}s)")
            raise ValueError(f"严重错误: 等待响应超时 (echo={echo})")
        result = future.result()
        del self.echo_dict[echo]
        return result

    async def _send_payload(self, payload: Payload) -> None:
        """发送 payload 到 WebSocket"""
        await self.websocket.send_text(payload.model_dump_json())

    async def _send_and_wait(self, payload: PayloadWithEcho) -> Response:
        """发送 payload 并等待响应"""
        await self._send_payload(payload)
        return await self.create_future(echo=payload.echo)
