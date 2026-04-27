"""Redis 消息存储服务。"""

import asyncio
import time
import uuid
from collections.abc import Awaitable
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlparse

import aiofiles
import httpx
from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.models import (
    At,
    GroupMessage,
    Image,
    MessageSegment,
    Meta,
    NapCatId,
    Notice,
    PrivateMessage,
    Reply,
    Request,
    Sender,
    Text,
    Video,
)
from app.utils.encoding import base64_to_bytes
from app.utils.file_type import detect_extension
from app.utils.log import log_event, log_exception
from app.utils.retry_utils import create_retry_manager

type StoredMessage = GroupMessage | PrivateMessage | Notice | Meta | Request
type SearchModel = type[GroupMessage] | type[PrivateMessage] | type[Notice] | type[Meta] | type[Request]
type RedisValue = str | bytes | bytearray

BASE64_FILE_PREFIX: Final[str] = "base64://"
DATA_URL_PREFIX: Final[str] = "data:"
DEFAULT_IMAGE_EXT: Final[str] = ".jpg"
DEFAULT_VIDEO_EXT: Final[str] = ".mp4"
LOCAL_FILE_MARKER: Final[str] = "local"
MEDIA_TYPE_IMAGE: Final[str] = "image"


class RedisDatabaseManager:
    """负责事件消息落盘、媒体下载和 Redis 检索。"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        path: str | Path,
        redis_client: Redis,
        consumers_count: int = 1,
    ) -> None:
        """初始化 Redis 数据库管理器。"""
        self.path: Path = Path(path)
        self.client: httpx.AsyncClient = client
        self.redis_client: Redis = redis_client
        self.path.mkdir(parents=True, exist_ok=True)
        self.task_queue: asyncio.Queue[StoredMessage] = asyncio.Queue()
        self.consumers_count: int = consumers_count
        self.consumers: list[asyncio.Task[None]] = []
        self._background_tasks: set[asyncio.Task[None]] = set()
        self.register_consumers()

    def register_consumers(self) -> None:
        """注册并启动消费者任务。"""
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self._consumer())
            self.consumers.append(consumer)

    async def stop_consumers(self) -> None:
        """取消并停止所有消费者任务。"""
        for consumer in self.consumers:
            _ = consumer.cancel()
        if self.consumers:
            try:
                _ = await asyncio.wait_for(
                    asyncio.gather(*self.consumers, return_exceptions=True), timeout=3
                )
            except asyncio.TimeoutError:
                log_event(
                    level="ERROR",
                    event="redis.consumer.stop_timeout",
                    category="redis",
                    message="Redis 数据库消费者关闭超时",
                )
            finally:
                self.consumers.clear()

    async def add_to_queue(self, msg: StoredMessage) -> None:
        """将事件消息加入异步存储队列。"""
        await self.task_queue.put(msg)

    async def search_messages(
        self,
        *,
        self_id: NapCatId,
        message_id: NapCatId | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: NapCatId | None = None,
        user_id: NapCatId | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> StoredMessage | list[StoredMessage] | None:
        """查询单条消息、分页消息或时间范围消息。"""
        hash_key, zset_key, target_model = self._build_search_target(
            self_id=self_id, group_id=group_id, user_id=user_id, root=root
        )
        raw_result = await self._search_raw(
            hash_key=hash_key,
            zset_key=zset_key,
            message_id=message_id,
            limit_tuple=limit_tuple,
            max_time=max_time,
            min_time=min_time,
        )
        if raw_result is None:
            return None
        if not isinstance(raw_result, list):
            return self._parse_stored_message(
                target_model=target_model,
                raw_value=raw_result,
            )
        return [
            self._parse_stored_message(
                target_model=target_model,
                raw_value=raw_msg,
            )
            for raw_msg in raw_result
            if raw_msg is not None
        ]

    def _parse_stored_message(
        self, *, target_model: SearchModel, raw_value: object
    ) -> StoredMessage:
        """把 Redis 原始 JSON 值收窄后解析为协议事件模型。"""
        if isinstance(raw_value, (str, bytes, bytearray)):
            return target_model.model_validate_json(raw_value)
        raise TypeError(f"Redis 消息 JSON 类型不合法: {type(raw_value).__name__}")

    async def _consumer(self) -> None:
        """持续从队列取出消息并存储。"""
        while True:
            msg = await self.task_queue.get()
            try:
                await self._store_msg(msg=msg)
            except Exception as exc:
                log_exception(
                    event="redis.store.exception",
                    category="redis",
                    message="Redis 消息存储失败",
                    exc=exc,
                    message_model=type(msg).__name__,
                )
            finally:
                self.task_queue.task_done()

    async def _store_msg(self, msg: StoredMessage) -> None:
        """存储消息到 Redis，并为收到的媒体消息保存本地路径。"""
        if isinstance(msg, (GroupMessage, PrivateMessage)):
            await self._save_media_resources(msg=msg)
        hash_key, zset_key, msg_id = self._build_redis_keys(msg=msg)
        await self.store_data(
            hash_key=hash_key,
            zset_key=zset_key,
            value=msg.model_dump_json(),
            msg_id=msg_id,
            time_id=msg.time,
        )

    async def store_data(
        self,
        hash_key: str,
        zset_key: str,
        value: str,
        msg_id: str | None = None,
        time_id: int | None = None,
    ) -> None:
        """将数据存入 Redis Hash 并在 ZSet 中记录时间索引。"""
        actual_msg_id = msg_id or str(uuid.uuid4())
        actual_time_id = time_id or int(time.time())
        pipe = self.redis_client.pipeline()
        _ = pipe.hset(hash_key, actual_msg_id, value)  # pyright: ignore[reportUnknownMemberType]
        _ = pipe.zadd(zset_key, {actual_msg_id: actual_time_id})
        _ = await pipe.execute()

    async def store_outgoing_message(
        self,
        *,
        self_id: NapCatId,
        message_id: NapCatId,
        message_segments: list[MessageSegment],
        group_id: NapCatId | None = None,
        user_id: NapCatId | None = None,
    ) -> None:
        """把机器人主动发送成功的消息写入 Redis，供后续引用消息检索。"""
        if group_id is None and user_id is None:
            raise ValueError("保存出站消息时必须指定 group_id 或 user_id")
        if group_id is not None and user_id is not None:
            raise ValueError("保存出站消息时不能同时指定 group_id 和 user_id")
        segments = [segment.model_copy(deep=True) for segment in message_segments]
        await self._save_outgoing_inline_images(
            message_id=message_id,
            message_segments=segments,
        )
        sender = Sender(user_id=self_id, nickname="机器人")
        message_time = int(time.time())
        raw_message = self._build_raw_message(message_segments=segments)
        if group_id is not None:
            msg = GroupMessage(
                time=message_time,
                self_id=self_id,
                post_type="message_sent",
                message_type="group",
                sub_type="normal",
                user_id=self_id,
                message_id=message_id,
                group_id=group_id,
                message=segments,
                raw_message=raw_message,
                sender=sender,
            )
        else:
            if user_id is None:
                raise ValueError("保存私聊出站消息时 user_id 不能为空")
            msg = PrivateMessage(
                time=message_time,
                self_id=self_id,
                post_type="message_sent",
                message_type="private",
                sub_type="friend",
                user_id=user_id,
                message_id=message_id,
                message=segments,
                raw_message=raw_message,
                sender=sender,
                group_id=None,
            )
        await self._store_msg(msg=msg)
        log_event(
            level="DEBUG",
            event="redis.outgoing_message.stored",
            category="redis",
            message="已保存机器人出站消息",
            self_id=self_id,
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
        )

    async def _save_outgoing_inline_images(
        self, *, message_id: NapCatId, message_segments: list[MessageSegment]
    ) -> None:
        """把出站消息中的内联 Base64 图片落成本地文件，避免 Redis 存大段图片。"""
        for index, segment in enumerate(message_segments):
            if not isinstance(segment, Image):
                continue
            source = segment.data.file
            if source.startswith(BASE64_FILE_PREFIX):
                image_bytes = base64_to_bytes(source.removeprefix(BASE64_FILE_PREFIX))
            elif source.startswith(DATA_URL_PREFIX):
                image_bytes = base64_to_bytes(source)
            else:
                self._attach_existing_outgoing_image_source(segment=segment)
                continue
            extension = detect_extension(image_bytes)
            if extension == ".unknown":
                extension = DEFAULT_IMAGE_EXT
            file_path = self.path / f"{message_id}_{index}{extension}"
            async with aiofiles.open(file_path, "wb") as file:
                _ = await file.write(image_bytes)
            segment.data.file = file_path.name
            segment.data.path = str(file_path)
            segment.data.summary = segment.data.summary or "[图片]"

    def _attach_existing_outgoing_image_source(self, *, segment: Image) -> None:
        """为 URL 或本地路径形式的出站图片补充可复用读取来源。"""
        source = segment.data.file
        if source.startswith(("http://", "https://")):
            segment.data.url = segment.data.url or source
            return
        source_path = Path(source)
        if source_path.is_file():
            segment.data.path = segment.data.path or str(source_path)

    def _build_raw_message(self, *, message_segments: list[MessageSegment]) -> str:
        """生成用于历史展示的简化 CQ 字符串。"""
        parts: list[str] = []
        for segment in message_segments:
            if isinstance(segment, Text):
                parts.append(segment.data.text)
            elif isinstance(segment, At):
                parts.append(f"[CQ:at,qq={segment.data.qq}]")
            elif isinstance(segment, Reply):
                parts.append(f"[CQ:reply,id={segment.data.id}]")
            elif isinstance(segment, Image):
                parts.append(f"[CQ:image,file={segment.data.file}]")
            else:
                parts.append(f"[CQ:{segment.type}]")
        return "".join(parts)

    async def _search_raw(
        self,
        *,
        hash_key: str,
        zset_key: str,
        message_id: NapCatId | None,
        limit_tuple: tuple[int, int] | None,
        max_time: int | None,
        min_time: int | None,
    ) -> RedisValue | list[RedisValue | None] | None:
        """按 Redis key 查询原始 JSON。"""
        if message_id is not None and not any((limit_tuple, max_time, min_time)):
            return await cast(
                Awaitable[RedisValue | None],
                self.redis_client.hget(hash_key, str(message_id)),
            )
        if limit_tuple is not None and message_id is None:
            offset, count = limit_tuple
            ids = await cast(
                Awaitable[list[RedisValue]],
                self.redis_client.zrevrange(  # pyright: ignore[reportUnknownMemberType]
                    zset_key, offset, offset + count - 1
                ),
            )
            return await self._load_messages_by_ids(hash_key=hash_key, ids=ids)
        if max_time is not None and min_time is not None and message_id is None:
            ids = await cast(
                Awaitable[list[RedisValue]],
                self.redis_client.zrevrangebyscore(  # pyright: ignore[reportUnknownMemberType]
                    zset_key, max=max_time, min=min_time
                ),
            )
            return await self._load_messages_by_ids(hash_key=hash_key, ids=ids)
        raise ValueError("消息查询参数组合不合法")

    async def _load_messages_by_ids(
        self, *, hash_key: str, ids: list[RedisValue]
    ) -> list[RedisValue | None] | None:
        """根据消息 ID 列表批量读取消息 JSON。"""
        if not ids:
            log_event(
                level="DEBUG",
                event="redis.search.empty",
                category="redis",
                message="未找到对应消息",
                hash_key=hash_key,
            )
            return None
        return await cast(
            Awaitable[list[RedisValue | None]],
            self.redis_client.hmget(hash_key, ids),  # pyright: ignore[reportUnknownMemberType]
        )

    def _build_search_target(
        self,
        *,
        self_id: NapCatId,
        group_id: NapCatId | None,
        user_id: NapCatId | None,
        root: str | None,
    ) -> tuple[str, str, SearchModel]:
        """根据查询参数确定 Redis key 和目标模型。"""
        if group_id is not None and user_id is None and root is None:
            return (
                f"bot:{self_id}:group:{group_id}:msg_data",
                f"bot:{self_id}:group:{group_id}:time_map",
                GroupMessage,
            )
        if user_id is not None and group_id is None and root is None:
            return (
                f"bot:{self_id}:private:{user_id}:msg_data",
                f"bot:{self_id}:private:{user_id}:time_map",
                PrivateMessage,
            )
        if root is not None and group_id is None and user_id is None:
            model_map: dict[str, SearchModel] = {
                "notice": Notice,
                "meta": Meta,
                "request": Request,
            }
            target_model = model_map.get(root)
            if target_model is None:
                raise ValueError(f"未知的 Root 类型: {root}")
            return (
                f"bot:{self_id}:{root}:msg_data",
                f"bot:{self_id}:{root}:time_map",
                target_model,
            )
        raise ValueError("消息查询目标参数不合法")

    def _build_redis_keys(self, msg: StoredMessage) -> tuple[str, str, str]:
        """根据事件类型生成 Redis 键名和消息 ID。"""
        id_val: NapCatId | None = None
        match msg:
            case GroupMessage():
                id_val = msg.group_id
                root = "group"
                msg_id = str(msg.message_id)
            case PrivateMessage():
                id_val = msg.user_id
                root = "private"
                msg_id = str(msg.message_id)
            case Notice():
                root = "notice"
                msg_id = str(uuid.uuid4())
            case Meta():
                root = "meta"
                msg_id = str(uuid.uuid4())
            case Request():
                root = "request"
                msg_id = str(uuid.uuid4())
        if id_val is not None:
            hash_key = f"bot:{msg.self_id}:{root}:{id_val}:msg_data"
            zset_key = f"bot:{msg.self_id}:{root}:{id_val}:time_map"
        else:
            hash_key = f"bot:{msg.self_id}:{root}:msg_data"
            zset_key = f"bot:{msg.self_id}:{root}:time_map"
        return hash_key, zset_key, msg_id

    async def _save_media_resources(
        self, msg: GroupMessage | PrivateMessage
    ) -> None:
        """遍历消息中的图片和视频并分发下载任务。"""
        for index, segment in enumerate(msg.message):
            if not isinstance(segment, (Image, Video)):
                continue
            await self._dispatch_media_download(msg=msg, segment=segment, index=index)

    async def _dispatch_media_download(
        self, segment: Image | Video, msg: GroupMessage | PrivateMessage, index: int
    ) -> None:
        """创建单个媒体下载任务。"""
        if segment.data.path:
            return
        url = segment.data.url
        if not url:
            log_event(
                level="WARNING",
                event="media.url.empty",
                category="media",
                message="媒体 URL 为空，跳过下载",
                message_id=str(msg.message_id),
                segment_index=index,
            )
            return
        parsed_path = Path(urlparse(url).path)
        extension = parsed_path.suffix
        if not extension:
            extension = (
                DEFAULT_IMAGE_EXT if segment.type == MEDIA_TYPE_IMAGE else DEFAULT_VIDEO_EXT
            )
        file_path = self.path / f"{msg.message_id}_{index}{extension}"
        segment.data.path = str(file_path)
        task = asyncio.create_task(
            self._download_single_segment(
                file_path=file_path, url=url, msg=msg, index=index
            )
        )
        self._create_task_reference(task=task)

    async def _download_single_segment(
        self,
        file_path: Path,
        url: str,
        msg: GroupMessage | PrivateMessage,
        index: int,
    ) -> None:
        """下载单个媒体资源到本地，失败时清空模型中的本地路径。"""
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
                        _ = response.raise_for_status()
                        async with aiofiles.open(file_path, "wb") as file:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                _ = await file.write(chunk)
        except Exception as exc:
            file_path.unlink(missing_ok=True)
            await asyncio.sleep(5)
            await self._clear_media_path(msg=msg, index=index)
            log_exception(
                event="media.download.exception",
                category="media",
                message="下载媒体资源失败",
                exc=exc,
                url=url,
                path=str(file_path),
                message_id=str(msg.message_id),
                segment_index=index,
            )

    async def _clear_media_path(
        self, msg: GroupMessage | PrivateMessage, index: int
    ) -> None:
        """下载失败时，将 Redis 中对应媒体的 path 置空。"""
        hash_key, _, msg_id = self._build_redis_keys(msg=msg)
        async with self.redis_client.pipeline() as pipe:
            while True:
                try:
                    _ = await pipe.watch(hash_key)
                    raw_json = await cast(
                        Awaitable[RedisValue | None],
                        self.redis_client.hget(hash_key, msg_id),
                    )
                    if not raw_json:
                        _ = await pipe.unwatch()
                        return
                    msg_obj = type(msg).model_validate_json(raw_json)
                    segment = msg_obj.message[index]
                    if isinstance(segment, (Image, Video)):
                        segment.data.path = None
                    pipe.multi()
                    _ = pipe.hset(hash_key, msg_id, msg_obj.model_dump_json())  # pyright: ignore[reportUnknownMemberType]
                    _ = await pipe.execute()
                    return
                except WatchError:
                    continue
                except Exception as exc:
                    log_exception(
                        event="media.path_clear.exception",
                        category="media",
                        message="清理媒体缓存路径失败",
                        exc=exc,
                        message_id=msg_id,
                        segment_index=index,
                    )
                    return

    def _create_task_reference(self, task: asyncio.Task[None]) -> None:
        """持有后台任务引用，防止被 GC 回收。"""
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def del_data(self, hash_key: str, zset_key: str, msg_id: str) -> None:
        """删除指定 Redis Hash 数据和时间索引。"""
        async with self.redis_client.pipeline(transaction=True) as pipe:
            _ = pipe.hdel(hash_key, msg_id)
            _ = pipe.zrem(zset_key, msg_id)
            _ = await pipe.execute()
