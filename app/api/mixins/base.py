"""基础 Mixin 类

提供 BOTClient 的基础功能，包括 WebSocket 通信和 Future 管理。
"""

import asyncio
import uuid
from typing import AsyncGenerator, Protocol, runtime_checkable

from fastapi import WebSocket

from app.database import RedisDatabaseManager
from app.models.events.response import Response, StreamData, StreamDataComplete, StreamDataError
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


class StreamTransmissionError(RuntimeError):
    def __init__(
        self,
        *,
        echo: str,
        status: str | None = None,
        retcode: int | None = None,
        message: str | None = None,
        wording: str | None = None,
    ) -> None:
        parts: list[str] = [f"echo={echo}"]
        if status is not None:
            parts.append(f"status={status}")
        if retcode is not None:
            parts.append(f"retcode={retcode}")
        if message:
            parts.append(f"message={message}")
        if wording:
            parts.append(f"wording={wording}")
        super().__init__("流式传输失败: " + ", ".join(parts))
        self.echo = echo
        self.status = status
        self.retcode = retcode
        self.message = message
        self.wording = wording


class BaseMixin:
    """基础 Mixin，提供 WebSocket 通信基础设施"""

    websocket: WebSocket
    database: RedisDatabaseManager
    echo_dict: dict[str, asyncio.Future[Response]]
    streams_dict: dict[str, asyncio.Queue[Response | None | BaseException]]
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
        stream_queue = self.streams_dict.get(echo)
        if stream_queue is not None and response.stream == "stream-action":
            if response.status != "ok" or response.retcode != 0:
                stream_queue.put_nowait(
                    StreamTransmissionError(
                        echo=echo,
                        status=response.status,
                        retcode=response.retcode,
                        message=response.message,
                        wording=response.wording,
                    )
                )
                return
            data = response.data
            if isinstance(data, StreamDataError):
                stream_queue.put_nowait(
                    StreamTransmissionError(
                        echo=echo,
                        status=response.status,
                        retcode=response.retcode,
                        message=data.message or response.message,
                        wording=response.wording,
                    )
                )
                return
            if isinstance(data, StreamDataComplete):
                stream_queue.put_nowait(None)
                return
            if isinstance(data, StreamData):
                stream_queue.put_nowait(response)
                return

            if isinstance(data, dict):
                data_type = data.get("data_type")
                data_kind = data.get("type")
                if data_kind == "response" and data_type in {"data_complete", "file_complete"}:
                    stream_queue.put_nowait(None)
                    return
                if data_kind == "error" or data_type == "error":
                    stream_queue.put_nowait(
                        StreamTransmissionError(
                            echo=echo,
                            status=response.status,
                            retcode=response.retcode,
                            message=response.message,
                            wording=response.wording,
                        )
                    )
                    return
                stream_queue.put_nowait(response)
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

    async def wait_stream(self, echo: str) -> AsyncGenerator[Response | None]:
        queue: asyncio.Queue[Response | None | BaseException] = asyncio.Queue()
        self.streams_dict[echo] = queue
        loop = asyncio.get_running_loop()
        start_ts = loop.time()
        last_ts = start_ts
        chunk_count = 0
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self.timeout)
                except asyncio.TimeoutError:
                    elapsed = loop.time() - start_ts
                    idle = loop.time() - last_ts
                    logger.error(
                        f"流式响应接收中断/超时 (echo={echo}, elapsed={elapsed:.3f}s, idle={idle:.3f}s, chunks={chunk_count})"
                    )
                    raise ValueError(f"流传输超时 (echo={echo})")
                if item is None:
                    elapsed = loop.time() - start_ts
                    logger.debug(
                        f"流式响应接收完成 (echo={echo}, elapsed={elapsed:.3f}s, chunks={chunk_count})"
                    )
                    break
                if isinstance(item, BaseException):
                    raise item
                last_ts = loop.time()
                if isinstance(item.data, StreamData) and item.data.data_type in {
                    "data_chunk",
                    "file_chunk",
                }:
                    chunk_count += 1
                yield item
        finally:
            self.streams_dict.pop(echo, None)

    async def _send_payload(self, payload: Payload) -> None:
        """发送 payload 到 WebSocket"""
        await self.websocket.send_text(payload.model_dump_json())

    async def _send_and_wait(self, payload: PayloadWithEcho) -> Response:
        """发送 payload 并等待响应"""
        await self._send_payload(payload)
        return await self.create_future(echo=payload.echo)
