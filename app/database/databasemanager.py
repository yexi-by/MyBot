import asyncio
from pathlib import Path
from urllib.parse import urlparse

import aiofiles
import httpx
from utils import logger

from app.models import GroupMessage, Image, Meta, Notice, PrivateMessage, Request, Video
from app.utils import create_retry_manager

from .schemas import Data, SessionData

type Message = GroupMessage | PrivateMessage | Notice | Meta | Request


class DatabaseManager:
    def __init__(
        self,
        client: httpx.AsyncClient,
        path: str | Path,
        consumers_count: int,
    ) -> None:
        self.path = Path(path)
        self.client = client
        self.qq_data: dict[int, Data] = {}
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
            try:
                await self._store_msg(msg=msg)
            except Exception as e:
                logger.warning(e)
            finally:
                self.task_queue.task_done()

    def register_consumers(self) -> None:
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self.consumer())
            self.consumers.append(consumer)

    async def _store_msg(
        self,
        msg: Message,
    ) -> None:
        data = self.qq_data.setdefault(msg.self_id, Data())
        match msg:
            case GroupMessage():
                await self._dispatch_media_download(msg=msg)
                target_session = data.group.setdefault(msg.group_id, SessionData())
                target_session.msg_data[msg.message_id] = msg
                target_session.time_map[msg.message_id] = msg.time
            case PrivateMessage():
                await self._dispatch_media_download(msg=msg)
                target_session = data.private.setdefault(msg.user_id, SessionData())
                target_session.msg_data[msg.message_id] = msg
                target_session.time_map[msg.message_id] = msg.time
            case Notice():
                data.notice.append(msg)
            case Meta():
                data.meta.append(msg)
            case Request():
                data.request.append(msg)
            case _:
                return

    async def _dispatch_media_download(
        self, msg: GroupMessage | PrivateMessage
    ) -> None:
        """分发下载任务"""
        async with asyncio.TaskGroup() as tg:
            for index, segment in enumerate(msg.message):
                if segment.type != "image" and segment.type != "video":
                    continue
                tg.create_task(self._download_single_segment(index, segment, msg))

    async def _download_single_segment(
        self, index: int, segment: Image | Video, msg: GroupMessage | PrivateMessage
    ) -> None:
        url = segment.data.url
        if not url:
            logger.warning(f"错误: {msg.message_id} 的第 {index} 个资源 URL 为空")
            return
        parsed_path = Path(urlparse(url).path)
        ext = parsed_path.suffix
        if not ext:
            ext = ".jpg" if segment.type == "image" else ".mp4"
        file_name = f"{msg.message_id}_{index}{ext}"
        file_path = self.path / file_name
        retryer = create_retry_manager(
            error_types=(
                httpx.TransportError,
                httpx.TimeoutException,
                httpx.HTTPStatusError,
            )
        )
        try:
            async for attempt in retryer:
                with attempt:
                    async with self.client.stream("GET", url) as response:
                        response.raise_for_status()
                        async with aiofiles.open(file_path, "wb") as f:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                await f.write(chunk)

            segment.data.local_path = str(file_path)
        except Exception as e:
            logger.error(f"下载资源失败: {url}，错误信息: {e}")
