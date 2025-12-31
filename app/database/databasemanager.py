import asyncio
from pathlib import Path
from urllib.parse import urlparse
from redis.asyncio import Redis
from redis.exceptions import WatchError
import aiofiles
import httpx
from utils import logger
import uuid
from app.models import GroupMessage, Image, Meta, Notice, PrivateMessage, Request, Video
from app.utils import create_retry_manager


type Message = GroupMessage | PrivateMessage | Notice | Meta | Request


class RedisDatabaseManager:
    def __init__(
        self,
        client: httpx.AsyncClient,
        path: str | Path,
        redis_client: Redis,
        consumers_count: int = 1,
    ) -> None:
        self.path = Path(path)
        self.client = client
        self.redis_client = redis_client
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
                logger.error(e)
            finally:
                self.task_queue.task_done()

    def register_consumers(self) -> None:
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self.consumer())
            self.consumers.append(consumer)

    async def stop_consumers(self) -> None:
        for consumer in self.consumers:
            consumer.cancel()
        if self.consumers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.consumers, return_exceptions=True), timeout=3
                )
            except asyncio.TimeoutError:
                logger.error("RedisDatabaseManager消费者关闭超时")
            finally:
                self.consumers.clear()

    async def update_message(self, msg: GroupMessage | PrivateMessage):
        match msg:
            case GroupMessage():
                id_val = msg.group_id
                root = "group"
            case PrivateMessage():
                id_val = msg.user_id
                root = "private"
            case _:
                raise ValueError("严重错误!")
        hash_key = f"bot:{msg.self_id}:{root}:{id_val}:msg_data"
        zset_key = f"bot:{msg.self_id}:{root}:{id_val}:time_map"
        async with self.redis_client.pipeline() as pipe:
            msg_id = str(msg.message_id)
            msg_json = msg.model_dump_json()
            pipe.hset(hash_key, msg_id, msg_json)
            pipe.zadd(zset_key, {msg_id: msg.time})
            await pipe.execute()
        await self._dispatch_media_download(msg=msg)

    async def update_other(self, msg: Notice | Meta | Request):
        match msg:
            case Notice():
                root = "notice"
            case Meta():
                root = "meta"
            case Request():
                root = "request"
            case _:
                raise ValueError("严重错误!")
        hash_key = f"bot:{msg.self_id}:{root}:msg_data"
        zset_key = f"bot:{msg.self_id}:{root}:time_map"
        async with self.redis_client.pipeline() as pipe:
            unique_id = str(uuid.uuid4())
            msg_json = msg.model_dump_json()
            pipe.hset(hash_key, unique_id, msg_json)
            pipe.zadd(zset_key, {unique_id: msg.time})
            await pipe.execute()

    async def _store_msg(
        self,
        msg: Message,
    ) -> None:
        match msg:
            case GroupMessage() | PrivateMessage():
                await self.update_message(msg=msg)
            case Notice() | Meta() | Request():
                await self.update_other(msg=msg)
            case _:
                return

    async def _dispatch_media_download(
        self, msg: GroupMessage | PrivateMessage
    ) -> None:
        """分发下载任务"""
        for index, segment in enumerate(msg.message):
            if segment.type != "image" and segment.type != "video":
                continue
            url = segment.data.url
            if not url:
                logger.warning(f"错误: {msg.message_id} 的第 {index} 个资源 URL 为空")
                continue
            parsed_path = Path(urlparse(url).path)
            ext = parsed_path.suffix
            if not ext:
                ext = ".jpg" if segment.type == "image" else ".mp4"
            file_name = f"{msg.message_id}_{index}{ext}"
            file_path = self.path / file_name
            segment.data.local_path = str(file_path)
            asyncio.create_task(
                self._download_single_segment(
                    file_path=file_path, url=url, msg=msg, index=index
                )
            )

    async def _download_single_segment(
        self, file_path: Path, url: str, msg: GroupMessage | PrivateMessage, index: int
    ) -> None:
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
        except Exception as e:
            match msg:
                case GroupMessage():
                    id_val = msg.group_id
                    root = "group"
                    Msg = GroupMessage
                case PrivateMessage():
                    id_val = msg.user_id
                    root = "private"
                    Msg = PrivateMessage
                case _:
                    raise ValueError("严重错误!")

            hash_key = f"bot:{msg.self_id}:{root}:{id_val}:msg_data"
            msg_id = str(msg.message_id)
            async with self.redis_client.pipeline() as pipe:  # 乐观锁
                while True:
                    try:
                        await pipe.watch(hash_key)
                        raw_json = await self.redis_client.hget(hash_key, msg_id)  # type: ignore
                        await asyncio.sleep(0.1)  # 防止过快导致的冲突
                        if not raw_json:
                            await pipe.unwatch()
                            return
                        msg_obj = Msg.model_validate_json(raw_json)
                        segment = msg_obj.message[index]
                        if isinstance(segment, (Image, Video)):
                            segment.data.local_path = None
                        new_json = msg_obj.model_dump_json()
                        pipe.multi()
                        pipe.hset(hash_key, msg_id, new_json)
                        await pipe.execute()
                        break
                    except WatchError:
                        continue
                    except Exception as e:
                        logger.error(e)
                        break
            logger.error(f"下载资源失败: {url}，错误信息: {e}")
