import asyncio
import base64
import time
import uuid
from pathlib import Path
from typing import Final, overload
from urllib.parse import urlparse
from pydantic import ValidationError
import aiofiles
import filetype
import httpx
from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.models import (
    GroupMessage,
    Image,
    Meta,
    Notice,
    PrivateMessage,
    Request,
    SelfMessage,
    Video,
)
from app.utils import create_retry_manager, logger

type Message = GroupMessage | PrivateMessage | Notice | Meta | Request | SelfMessage

# ==================== 常量定义 ====================
BASE64_PREFIX: Final[str] = "base64://"
BASE64_PREFIX_LEN: Final[int] = len(BASE64_PREFIX)
BASE64_MODULO: Final[int] = 4
BASE64_PADDING_CHAR: Final[str] = "="

# 文件相关常量
DEFAULT_IMAGE_EXT: Final[str] = ".jpg"
DEFAULT_VIDEO_EXT: Final[str] = ".mp4"
DEFAULT_BINARY_EXT: Final[str] = ".bin"
LOCAL_FILE_MARKER: Final[str] = "local"

# 媒体类型常量
MEDIA_TYPE_IMAGE: Final[str] = "image"
MEDIA_TYPE_VIDEO: Final[str] = "video"

# 分块大小 (必须是 BASE64_MODULO 的倍数)
CHUNK_SIZE: Final[int] = 1024 * 1024  # 1MB
HEADER_SAMPLE_SIZE: Final[int] = 260
DATA_URI_SEARCH_LIMIT: Final[int] = 200


def fix_base64_padding(data: str) -> str:
    """修正 Base64 字符串的 padding，确保长度是 4 的倍数。"""
    padding_needed = (BASE64_MODULO - len(data) % BASE64_MODULO) % BASE64_MODULO
    return data + BASE64_PADDING_CHAR * padding_needed


def strip_base64_prefix(data: str) -> str:
    """移除 base64:// 前缀（如果存在）。"""
    if data.startswith(BASE64_PREFIX):
        return data[BASE64_PREFIX_LEN:]
    return data


class RedisDatabaseManager:
    # ==================== 初始化与生命周期 ====================

    def __init__(
        self,
        client: httpx.AsyncClient,
        path: str | Path,
        redis_client: Redis,
        consumers_count: int = 1,
    ) -> None:
        """初始化 Redis 数据库管理器，配置存储路径和消费者。"""
        self.path = Path(path)
        self.client = client
        self.redis_client = redis_client
        self.path.mkdir(parents=True, exist_ok=True)
        self.task_queue: asyncio.Queue[Message] = asyncio.Queue()
        self.consumers_count = consumers_count
        self.consumers: list[asyncio.Task] = []
        self._background_tasks: set[asyncio.Task] = set()
        self.register_consumers()

    def register_consumers(self) -> None:
        """注册并启动指定数量的消费者任务。"""
        for _ in range(self.consumers_count):
            consumer = asyncio.create_task(self._consumer())
            self.consumers.append(consumer)

    async def stop_consumers(self) -> None:
        """取消并停止所有消费者任务。"""
        for consumer in self.consumers:
            consumer.cancel()
        if self.consumers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.consumers, return_exceptions=True), timeout=3
                )
            except asyncio.TimeoutError:
                logger.error("Redis数据库消费者关闭超时")
            finally:
                self.consumers.clear()

    # ==================== 公共 API ====================

    async def add_to_queue(self, msg: Message) -> None:
        """将消息添加到异步任务队列中等待处理。"""
        await self.task_queue.put(msg)

    @overload
    async def search_messages(
        self,
        *,
        self_id: int,
        group_id: int,
        message_id: int,
    ) -> GroupMessage | SelfMessage | None: ...

    """查询群聊单条消息"""

    @overload
    async def search_messages(
        self,
        *,
        self_id: int,
        user_id: int,
        message_id: int,
    ) -> PrivateMessage | SelfMessage | None: ...

    """查询私聊单条消息"""

    @overload
    async def search_messages(
        self, *, self_id: int, limit_tuple: tuple[int, int], group_id: int
    ) -> list[GroupMessage] | None: ...

    """查询群聊最新消息列表(分页用: offset, count)"""

    @overload
    async def search_messages(
        self, *, self_id: int, limit_tuple: tuple[int, int], user_id: int
    ) -> list[PrivateMessage] | None: ...

    """查询私聊最新消息列表(分页用: offset, count)"""

    @overload
    async def search_messages(
        self, *, self_id: int, max_time: int, min_time: int, group_id: int
    ) -> list[GroupMessage] | None: ...

    """查询群聊时间段消息列表"""

    async def search_messages(
        self,
        *,
        self_id: int,
        message_id: int | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: int | None = None,
        user_id: int | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> Message | list[GroupMessage] | list[PrivateMessage] | list[Message] | None:
        """查询消息，支持单条、分页和时间范围查询。"""

        def parse_results(
            raw_msgs: list[str], model_cls: type[Message]
        ) -> list[Message]:
            return [
                model_cls.model_validate_json(raw_msg)
                for raw_msg in raw_msgs
                if raw_msg
            ]

        hash_key = ""
        zset_key = ""
        match (group_id, user_id, root):
            case (group_id, None, None):
                hash_key = f"bot:{self_id}:group:{group_id}:msg_data"
                zset_key = f"bot:{self_id}:group:{group_id}:time_map"
                TargetModel = GroupMessage
            case (None, user_id, None):
                hash_key = f"bot:{self_id}:private:{user_id}:msg_data"
                zset_key = f"bot:{self_id}:private:{user_id}:time_map"
                TargetModel = PrivateMessage
            case (None, None, root):
                hash_key = f"bot:{self_id}:{root}:msg_data"
                zset_key = f"bot:{self_id}:{root}:time_map"
                match root:
                    case "notice":
                        TargetModel = Notice
                    case "meta":
                        TargetModel = Meta
                    case "request":
                        TargetModel = Request
                    case _:
                        raise ValueError("未知的 Root 类型，无法查询对应消息")
            case _:
                raise ValueError("错误")

        match (message_id, limit_tuple, max_time, min_time):
            case (message_id, None, None, None):
                # 单条查询
                raw_json = await self.search_data(
                    hash_key=hash_key, msg_id=str(message_id)
                )
                if not raw_json:
                    return None
                if isinstance(raw_json, (str, bytes)):
                    try:
                        raw_msgs = TargetModel.model_validate_json(raw_json)
                    except ValidationError:  # 回复自己的消息 用SelfMessage这个格式
                        raw_msgs = SelfMessage.model_validate_json(raw_json)
                    return raw_msgs

            case (None, limit_tuple, max_time, min_time):
                # 按照时间查询
                if limit_tuple:
                    raw_msgs = await self.search_data(
                        hash_key=hash_key, zset_key=zset_key, limit_tuple=limit_tuple
                    )
                else:
                    raw_msgs = await self.search_data(
                        hash_key=hash_key,
                        zset_key=zset_key,
                        max_time=max_time,
                        min_time=min_time,
                    )
                if not raw_msgs:
                    return None
                if isinstance(raw_msgs, list):
                    return parse_results(raw_msgs, TargetModel)
            case _:
                raise ValueError("参数错误，无法查询对应消息")

    # ==================== 消息存储流程 ====================

    async def _consumer(self) -> None:
        """消费者协程，持续从队列中取出消息并存储。"""
        while True:
            msg = await self.task_queue.get()
            try:
                await self._store_msg(msg=msg)
            except Exception as e:
                logger.exception(e)
            finally:
                self.task_queue.task_done()

    async def _store_msg(self, msg: Message) -> None:
        """存储消息到 Redis，包括媒体资源的保存。"""
        match msg:
            case GroupMessage() | PrivateMessage() | SelfMessage():
                await self._save_media_resources(msg=msg)

        hash_key, zset_key, msg_id = self._build_redis_keys(msg=msg)
        msg_json = msg.model_dump_json()
        await self.store_data(
            hash_key=hash_key,
            zset_key=zset_key,
            value=msg_json,
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
        if not msg_id:
            msg_id = str(uuid.uuid4())
        if not time_id:
            time_id = int(time.time())
        pipe = self.redis_client.pipeline()
        pipe.hset(hash_key, msg_id, value)
        pipe.zadd(zset_key, {msg_id: time_id})
        await pipe.execute()

    async def search_data(
        self,
        *,
        hash_key: str,
        msg_id: str | None = None,
        zset_key: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> str | list[str] | None:
        """从 Redis 中查询数据，支持单条、分页和时间范围查询。"""
        match (msg_id, zset_key, limit_tuple, max_time, min_time):
            case (str(msg_id), None, None, None, None):
                return await self.redis_client.hget(hash_key, msg_id)  # type: ignore
            case (None, str(zset_key), (offset, count), None, None):
                start = offset
                end = offset + count - 1
                ids = await self.redis_client.zrevrange(zset_key, start, end)
                if not ids:
                    logger.warning("未找到对应消息")
                    return None
                return await self.redis_client.hmget(hash_key, ids)  # type: ignore

            case (None, str(zset_key), None, int(max_time), int(min_time)):
                ids = await self.redis_client.zrevrangebyscore(
                    zset_key, max=max_time, min=min_time
                )
                if not ids:
                    logger.warning("未找到对应消息")
                    return None
                return await self.redis_client.hmget(hash_key, ids)  # type: ignore
            case _:
                raise ValueError("严重错误")

    def _build_redis_keys(self, msg: Message):
        """根据消息类型生成对应的 Redis 键名和消息 ID。"""
        id_val = None
        match msg:
            case GroupMessage():
                id_val = msg.group_id
                root = "group"
                msg_id = str(msg.message_id)
            case SelfMessage():
                if msg.group_id:
                    id_val = msg.group_id
                    root = "group"
                else:
                    id_val = msg.user_id
                    root = "private"
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
            case _:
                raise ValueError("严重错误!")
        if id_val:
            hash_key = f"bot:{msg.self_id}:{root}:{id_val}:msg_data"
            zset_key = f"bot:{msg.self_id}:{root}:{id_val}:time_map"
        else:
            hash_key = f"bot:{msg.self_id}:{root}:msg_data"
            zset_key = f"bot:{msg.self_id}:{root}:time_map"
        return hash_key, zset_key, msg_id

    # ==================== 媒体资源处理 ====================

    async def _save_media_resources(
        self, msg: SelfMessage | GroupMessage | PrivateMessage
    ):
        """遍历消息中的媒体资源并分发保存任务。"""
        for index, segment in enumerate(msg.message):
            if not isinstance(segment, (Image, Video)):
                continue
            match msg:
                case GroupMessage() | PrivateMessage():
                    await self._dispatch_media_download(
                        msg=msg, segment=segment, index=index
                    )
                case SelfMessage():
                    image_base64 = segment.data.file
                    segment.data.file = LOCAL_FILE_MARKER
                    await self._save_self_media(
                        segment=segment,
                        index=index,
                        message_id=msg.message_id,
                        msg=msg,
                        image_base64=image_base64,
                    )

    async def _dispatch_media_download(
        self, segment: Image | Video, msg: GroupMessage | PrivateMessage, index: int
    ) -> None:
        """为群聊/私聊消息创建媒体下载任务并加入后台执行。"""
        url = segment.data.url
        if not url:
            logger.warning(f"错误: {msg.message_id} 的第 {index} 个资源 URL 为空")
            return
        parsed_path = Path(urlparse(url).path)
        extension = parsed_path.suffix
        if not extension:
            extension = (
                DEFAULT_IMAGE_EXT
                if segment.type == MEDIA_TYPE_IMAGE
                else DEFAULT_VIDEO_EXT
            )
        file_name = f"{msg.message_id}_{index}{extension}"
        file_path = self.path / file_name
        segment.data.local_path = str(file_path)
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
        """下载单个媒体资源到本地，失败时更新 Redis 中的路径为空。"""
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
        # 由于下载失败的情况真的非常少,我在使用napcat的时候从来都没有遇到
        # 所以假设所有的图片和视频都正确下载了 下面这段错误处理只是预防,毕竟我没遇到过,不代表不会发生
        except Exception as error:
            file_path.unlink(missing_ok=True)
            await asyncio.sleep(5)  # 防止redis还没写入 就下载失败
            await self._clear_media_local_path(msg=msg, index=index)
            logger.exception(f"下载资源失败: {url}，错误信息: {error}")

    async def _save_self_media(
        self,
        segment: Image | Video,
        message_id: int,
        index: int,
        msg: SelfMessage,
        image_base64: str,
    ):
        """保存自发消息中的 Base64 媒体资源到本地文件。"""
        file_base64 = image_base64
        data_start_index = 0

        # 查找 Data URI 中的逗号分隔符
        comma_index = file_base64.find(",", 0, DATA_URI_SEARCH_LIMIT)
        if comma_index != -1:
            data_start_index = comma_index + 1

        # 处理 base64:// 前缀
        actual_base64 = file_base64[data_start_index:]
        actual_base64 = strip_base64_prefix(actual_base64)
        if file_base64[data_start_index:].startswith(BASE64_PREFIX):
            data_start_index += BASE64_PREFIX_LEN

        # 获取头部用于检测文件类型
        header_slice = actual_base64[:HEADER_SAMPLE_SIZE]
        header_slice_padded = fix_base64_padding(header_slice)

        try:
            first_bytes = base64.b64decode(header_slice_padded)
            kind = filetype.guess(first_bytes)
            extension = f".{kind.extension}" if kind else DEFAULT_BINARY_EXT
        except Exception as e:
            logger.exception(e)
            extension = DEFAULT_BINARY_EXT

        file_name = f"{message_id}_{index}{extension}"
        file_path = self.path / file_name
        segment.data.local_path = str(file_path)
        task = asyncio.create_task(
            self._save_base64_to_file(
                file_path=file_path,
                file_base64=file_base64,
                data_start_index=data_start_index,
                msg=msg,
                index=index,
            )
        )
        self._create_task_reference(task=task)

    async def _save_base64_to_file(
        self,
        file_path: Path,
        file_base64: str,
        data_start_index: int,
        msg: SelfMessage,
        index: int,
    ):
        """将 Base64 编码的媒体数据分块解码并写入文件。"""
        # 处理 base64:// 前缀
        actual_base64 = file_base64[data_start_index:]
        actual_base64 = strip_base64_prefix(actual_base64)

        total_length = len(actual_base64)

        try:
            async with aiofiles.open(file_path, "wb") as f:
                for i in range(0, total_length, CHUNK_SIZE):
                    chunk = actual_base64[i : i + CHUNK_SIZE]
                    # 对于最后一个块，添加必要的 padding
                    is_last_chunk = i + CHUNK_SIZE >= total_length
                    if is_last_chunk:
                        chunk = fix_base64_padding(chunk)
                    decoded_chunk = await asyncio.to_thread(base64.b64decode, chunk)
                    await f.write(decoded_chunk)
        except Exception as e:
            logger.exception(e)
            file_path.unlink(missing_ok=True)
            await self._clear_media_local_path(msg=msg, index=index)

    async def _clear_media_local_path(
        self, msg: GroupMessage | PrivateMessage | SelfMessage, index: int
    ):
        """下载失败时，将 Redis 中消息的指定媒体本地路径置空。"""
        hash_key, zset_key, msg_id = self._build_redis_keys(msg=msg)
        async with self.redis_client.pipeline() as pipe:  # 乐观锁
            while True:
                try:
                    await pipe.watch(hash_key)
                    raw_json = await self.redis_client.hget(hash_key, msg_id)  # type: ignore
                    if not raw_json:
                        await pipe.unwatch()
                        return
                    msg_obj = type(msg).model_validate_json(raw_json)
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
                    logger.exception(e)
                    break

    # ==================== 工具方法 ====================

    def _create_task_reference(self, task: asyncio.Task):
        """持有后台任务引用，防止被 GC 回收，完成后自动清理。"""
        self._background_tasks.add(
            task
        )  # 让变量持有asyncio.Task任务引用，防止在某些情况被gc回收
        task.add_done_callback(
            self._background_tasks.discard
        )  # 做完自动删除 防内存泄漏

    async def del_data(self, hash_key: str, zset_key: str, msg_id: str) -> None:
        async with self.redis_client.pipeline(transaction=True) as pipe:
            pipe.hdel(hash_key, msg_id)
            pipe.zrem(zset_key, msg_id)
            await pipe.execute()
