from app.models import GroupMessage, PrivateMessage, Notice, Meta, Request
from .schemas import SessionData, Data
import httpx
from pathlib import Path
import aiofiles
from utils import logger
from urllib.parse import urlparse
import os
import asyncio

type Message = GroupMessage | PrivateMessage | Notice | Meta | Request


class QQData:
    def __init__(
        self,
        qq_id: int,
        client: httpx.AsyncClient,
        path: str | Path,
        consumers_count: int,
    ) -> None:
        self.path = Path(path)
        self.client = client
        self.qq_id = qq_id
        self.data = Data()
        self.path.mkdir(parents=True, exist_ok=True)
        self.task_queue: asyncio.Queue[Message] = asyncio.Queue()
        self.consumers_count = consumers_count
        self.consumers: list[asyncio.Task] = []
        self.register_consumers()

    async def add_to_queue(self, msg: Message) -> None:
        await self.task_queue.put(msg)

    async def consumer(self) -> None:
        while True:
            msg = await self.task_queue.get()
            await self._store_msg(msg=msg)
            self.task_queue.task_done()

    def register_consumers(self) -> None:
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self.consumer())
            self.consumers.append(consumer)

    async def _store_msg(
        self,
        msg: Message,
    ) -> None:
        match msg:
            case GroupMessage():
                await self._store_media(msg=msg)
                target_session = self.data.group.setdefault(msg.group_id, SessionData())
                target_session.msg_data[msg.message_id] = msg
                target_session.time_map[msg.message_id] = msg.time
            case PrivateMessage():
                await self._store_media(msg=msg)
                target_session = self.data.private.setdefault(
                    msg.user_id, SessionData()
                )
                target_session.msg_data[msg.message_id] = msg
                target_session.time_map[msg.message_id] = msg.time
            case Notice():
                self.data.notice.append(msg)
            case Meta():
                self.data.meta.append(msg)
            case Request():
                self.data.request.append(msg)
            case _:
                return

    async def _store_media(self, msg: GroupMessage | PrivateMessage) -> None:
        for index, segment in enumerate(msg.message):
            if segment.type != "image" and segment.type != "video":
                continue
            url = segment.data.url
            if not url:
                logger.warning(
                    f"Warning: {msg.message_id} 的第 {index} 个资源 URL 为空"
                )
                continue
            parsed_path = urlparse(url).path
            ext = os.path.splitext(parsed_path)[1]
            if not ext:
                ext = ".jpg" if segment.type == "image" else ".mp4"
            file_name = f"{msg.message_id}_{index}{ext}"
            file_path = self.path / file_name
            async with self.client.stream("GET", url) as response:
                response.raise_for_status()
                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        await f.write(chunk)
            segment.data.local_path = str(file_path)
