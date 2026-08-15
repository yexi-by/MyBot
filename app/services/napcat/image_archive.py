"""NapCat 群图片的内容寻址归档与可恢复下载 worker。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

import aiofiles
import filetype  # pyright: ignore[reportMissingTypeStubs]
import httpx

from app.models import ImageArchiveTask, StoredImage
from app.services.napcat.image_reader import (
    NapCatImageBot,
    NapCatImageReader,
    NapCatImageReadResult,
    NapCatImageResource,
)
from app.utils.log import log_event, log_exception

MAX_ARCHIVE_IMAGE_BYTES = 50 * 1024 * 1024
DEFAULT_ARCHIVE_CONCURRENCY = 8
DEFAULT_ARCHIVE_READ_TIMEOUT_SECONDS = 30.0
DEFAULT_ARCHIVE_LEASE_SECONDS = 60.0
DEFAULT_ARCHIVE_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_ARCHIVE_RETRY_DELAYS_SECONDS = (5.0, 30.0, 300.0)
MAX_ARCHIVE_ATTEMPTS = 1 + len(DEFAULT_ARCHIVE_RETRY_DELAYS_SECONDS)


class ImageArchiveError(ValueError):
    """图片归档内容不符合存储要求。"""


class ImageTooLargeError(ImageArchiveError):
    """图片超过单文件大小限制。"""


class InvalidImageContentError(ImageArchiveError):
    """文件内容无法识别为图片。"""


class InvalidInlineImageSourceError(ImageArchiveError):
    """内联图片不是支持的 base64 或图片 data URL。"""


class _DetectedFileType(Protocol):
    """表达 filetype 库检测结果中实际使用的字段。"""

    @property
    def extension(self) -> str:
        """返回由文件内容确定的扩展名。"""
        ...

    @property
    def mime(self) -> str:
        """返回由文件内容确定的 MIME。"""
        ...


class ImageArchiveReader(Protocol):
    """图片归档仅需要的单图读取能力。"""

    async def read(
        self, *, resource: NapCatImageResource
    ) -> NapCatImageReadResult:
        """读取一张 NapCat 图片。"""
        ...


class ImageArchiveTaskRepository(Protocol):
    """图片任务仓库的最小租约接口。

    仓库必须为每次认领生成不可预测的新 lease_token，并且只接受
    当前租约令牌的 complete 或 fail 写入。
    """

    async def claim_ready(
        self,
        *,
        bot_id: str,
        limit: int,
        lease_seconds: float,
    ) -> Sequence[ImageArchiveTask]:
        """仅为指定机器人原子认领可执行或租约已过期的任务。"""
        ...

    async def complete(
        self,
        *,
        task_id: int,
        lease_token: str,
        image: StoredImage,
    ) -> bool:
        """仅在租约仍属于调用方时完成任务。"""
        ...

    async def fail(
        self,
        *,
        task_id: int,
        lease_token: str,
        retry_at: datetime | None,
    ) -> bool:
        """仅在租约仍属于调用方时写入失败状态。"""
        ...


class ImageStore:
    """校验图片内容并按 SHA-256 原子写入本地存储。"""

    def __init__(
        self,
        *,
        root: Path,
        max_image_bytes: int = MAX_ARCHIVE_IMAGE_BYTES,
    ) -> None:
        """设置图片根目录和单文件大小限制。"""
        if max_image_bytes < 1:
            raise ValueError("单张图片大小限制必须大于等于 1")
        self.root: Path = root
        self.max_image_bytes: int = max_image_bytes

    async def store(self, *, image_bytes: bytes) -> StoredImage:
        """按实际内容识别图片类型，去重后原子写入。"""
        image_size = len(image_bytes)
        if image_size > self.max_image_bytes:
            raise ImageTooLargeError(
                f"图片大小 {image_size} 字节超过上限 "
                f"{self.max_image_bytes} 字节"
            )

        guess_file_type = cast(
            Callable[[bytes], _DetectedFileType | None],
            filetype.guess,
        )
        detected = guess_file_type(image_bytes)
        if detected is None or not detected.mime.startswith("image/"):
            raise InvalidImageContentError("文件内容无法识别为图片")

        digest = hashlib.sha256(image_bytes).hexdigest()
        storage_path = Path(
            digest[:2],
            digest[2:4],
            f"{digest}.{detected.extension.lower()}",
        )
        destination = self.root / storage_path
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)

        if not await asyncio.to_thread(destination.is_file):
            await self._publish_atomically(
                destination=destination,
                image_bytes=image_bytes,
            )

        return StoredImage(
            storage_key=storage_path.as_posix(),
            mime_type=detected.mime,
            size_bytes=image_size,
        )

    async def _publish_atomically(
        self,
        *,
        destination: Path,
        image_bytes: bytes,
    ) -> None:
        """在目标目录写完临时文件后一次性发布。"""
        temporary = destination.with_name(
            f".{destination.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            async with aiofiles.open(temporary, mode="xb") as target:
                await target.write(image_bytes)
                await target.flush()
            await asyncio.to_thread(os.replace, temporary, destination)
        finally:
            try:
                await asyncio.to_thread(temporary.unlink, missing_ok=True)
            except OSError as exc:
                log_exception(
                    event="napcat.image_archive.temp_cleanup_failed",
                    category="napcat_tools",
                    message="清理图片归档临时文件失败",
                    exc=exc,
                    path=str(temporary),
                )


@dataclass(frozen=True, slots=True)
class InlineImageArchiveResult:
    """出站内联图片归档后可供消息记录使用的路径。"""

    absolute_path: Path

    def __post_init__(self) -> None:
        """对外返回的文件路径必须已经是绝对路径。"""
        if not self.absolute_path.is_absolute():
            raise ValueError("absolute_path 必须是绝对路径")


class InlineImageArchiver:
    """严格解码并归档出站 base64 内联图片。"""

    def __init__(self, *, store: ImageStore) -> None:
        """复用同一内容寻址存储和大小上限。"""
        self.store: ImageStore = store

    async def archive(self, *, source: str) -> InlineImageArchiveResult:
        """解码 base64:// 或图片 data URL，并返回最终文件的绝对路径。"""
        payload = self._extract_payload(source=source)
        image_bytes = self._decode_payload(payload=payload)
        stored = await self.store.store(image_bytes=image_bytes)

        root = await asyncio.to_thread(self.store.root.resolve)
        absolute_path = await asyncio.to_thread(
            (root / Path(stored.storage_key)).resolve,
            strict=True,
        )
        if not absolute_path.is_relative_to(root):
            raise RuntimeError("内联图片存储路径越出图片根目录")
        return InlineImageArchiveResult(
            absolute_path=absolute_path,
        )

    def _extract_payload(self, *, source: str) -> str:
        """识别支持的内联前缀，并严格检查 data URL 声明。"""
        normalized_source = source.casefold()
        if normalized_source.startswith("base64://"):
            payload = source[len("base64://") :]
        elif normalized_source.startswith("data:"):
            header, separator, payload = source.partition(",")
            if separator == "":
                raise InvalidInlineImageSourceError("data URL 缺少数据分隔符")
            metadata = header[len("data:") :].split(";")
            mime_type = metadata[0]
            if re.fullmatch(
                r"image/[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-]*",
                mime_type,
                flags=re.IGNORECASE,
            ) is None:
                raise InvalidInlineImageSourceError(
                    "data URL 必须声明有效的图片 MIME"
                )
            if len(metadata) < 2 or metadata[-1].lower() != "base64":
                raise InvalidInlineImageSourceError(
                    "data URL 必须使用 base64 编码"
                )
            if any(item == "" or item.lower() == "base64" for item in metadata[1:-1]):
                raise InvalidInlineImageSourceError("data URL 参数无效")
        else:
            raise InvalidInlineImageSourceError(
                "内联图片必须使用 base64:// 或 data:image/...;base64,"
            )

        if payload == "":
            raise InvalidInlineImageSourceError("内联图片 base64 内容不能为空")
        return payload

    def _decode_payload(self, *, payload: str) -> bytes:
        """在分配解码结果前预判大小，并启用严格 base64 校验。"""
        max_encoded_length = 4 * ((self.store.max_image_bytes + 2) // 3)
        if len(payload) > max_encoded_length:
            raise ImageTooLargeError(
                f"内联图片 base64 长度超过 "
                f"{self.store.max_image_bytes} 字节图片的可能范围"
            )
        try:
            image_bytes = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidInlineImageSourceError(
                f"内联图片 base64 无效: {exc}"
            ) from exc
        if len(image_bytes) > self.store.max_image_bytes:
            raise ImageTooLargeError(
                f"图片大小 {len(image_bytes)} 字节超过上限 "
                f"{self.store.max_image_bytes} 字节"
            )
        return image_bytes


class ImageArchiveWorker:
    """带租约和有限重试的并发图片归档 worker。"""

    def __init__(
        self,
        *,
        bot_id: str,
        repository: ImageArchiveTaskRepository,
        reader: ImageArchiveReader,
        store: ImageStore,
        concurrency: int = DEFAULT_ARCHIVE_CONCURRENCY,
        read_timeout_seconds: float = DEFAULT_ARCHIVE_READ_TIMEOUT_SECONDS,
        lease_seconds: float = DEFAULT_ARCHIVE_LEASE_SECONDS,
        poll_interval_seconds: float = DEFAULT_ARCHIVE_POLL_INTERVAL_SECONDS,
        retry_delays_seconds: tuple[float, float, float] = (
            DEFAULT_ARCHIVE_RETRY_DELAYS_SECONDS
        ),
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        """保存 worker 依赖并检查并发、超时、租约和重试边界。"""
        if bot_id.strip() == "":
            raise ValueError("图片归档 bot_id 不能为空")
        if concurrency < 1:
            raise ValueError("图片归档并发数必须大于等于 1")
        if read_timeout_seconds <= 0:
            raise ValueError("图片归档读取超时必须大于 0")
        if lease_seconds <= 0:
            raise ValueError("图片归档租约时间必须大于 0")
        if poll_interval_seconds <= 0:
            raise ValueError("图片归档轮询间隔必须大于 0")
        if len(retry_delays_seconds) != 3:
            raise ValueError("图片归档必须配置三个重试间隔")
        if any(delay <= 0 for delay in retry_delays_seconds):
            raise ValueError("图片归档重试间隔必须全部大于 0")
        self.bot_id: str = bot_id
        self.repository: ImageArchiveTaskRepository = repository
        self.reader: ImageArchiveReader = reader
        self.store: ImageStore = store
        self.concurrency: int = concurrency
        self.read_timeout_seconds: float = read_timeout_seconds
        self.lease_seconds: float = lease_seconds
        self.poll_interval_seconds: float = poll_interval_seconds
        self.retry_delays_seconds: tuple[float, float, float] = (
            retry_delays_seconds
        )
        self._utc_now: Callable[[], datetime] = utc_now or (
            lambda: datetime.now(UTC)
        )

    async def run_once(self) -> int:
        """认领一批任务并在并发限制内完整处理。"""
        tasks = list(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=self.concurrency,
                lease_seconds=self.lease_seconds,
            )
        )
        if not tasks:
            return 0

        semaphore = asyncio.Semaphore(self.concurrency)

        async def process(task: ImageArchiveTask) -> None:
            async with semaphore:
                await self._process_task(task=task)

        await asyncio.gather(*(process(task) for task in tasks))
        return len(tasks)

    async def run(self, *, stop_event: asyncio.Event) -> None:
        """持续处理任务；仓库短暂失败时保留 worker 以便恢复。"""
        while not stop_event.is_set():
            try:
                processed_count = await self.run_once()
            except Exception as exc:
                log_exception(
                    event="napcat.image_archive.batch_failed",
                    category="napcat_tools",
                    message="图片归档 worker 认领或处理任务失败",
                    exc=exc,
                )
                processed_count = 0

            if processed_count > 0:
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                pass

    async def _process_task(self, *, task: ImageArchiveTask) -> None:
        """执行一次读取和存储，任何可恢复失败都转为任务状态。"""
        try:
            async with asyncio.timeout(self.read_timeout_seconds):
                read_result = await self.reader.read(
                    resource=self._resource_for_task(task=task)
                )
            if not read_result.ok:
                await self._record_failure(
                    task=task,
                    error_type=read_result.error_type or "ImageContentUnavailable",
                    error=read_result.error or "图片没有可读取内容",
                )
                return
            if read_result.source is None or read_result.image_bytes is None:
                await self._record_failure(
                    task=task,
                    error_type="ImageReaderProtocolError",
                    error="图片读取成功结果缺少来源或字节内容",
                )
                return
            stored = await self.store.store(image_bytes=read_result.image_bytes)
        except Exception as exc:
            await self._record_failure(
                task=task,
                error_type=type(exc).__name__,
                error=str(exc) or type(exc).__name__,
            )
            return

        try:
            completed = await self.repository.complete(
                task_id=task.task_id,
                lease_token=task.lease_token,
                image=stored,
            )
        except Exception as exc:
            log_exception(
                event="napcat.image_archive.completion_failed",
                category="napcat_tools",
                message="图片已归档，但任务完成状态写入失败",
                exc=exc,
                task_id=task.task_id,
                attempt_number=task.attempt_number,
                storage_key=stored.storage_key,
            )
            return

        if not completed:
            log_event(
                level="WARNING",
                event="napcat.image_archive.lease_lost",
                category="napcat_tools",
                message="图片归档完成时租约已丢失",
                task_id=task.task_id,
                attempt_number=task.attempt_number,
            )
            return

        log_event(
            level="DEBUG",
            event="napcat.image_archive.completed",
            category="napcat_tools",
            message="图片归档完成",
            task_id=task.task_id,
            attempt_number=task.attempt_number,
            source=read_result.source,
            storage_key=stored.storage_key,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
        )

    async def _record_failure(
        self,
        *,
        task: ImageArchiveTask,
        error_type: str,
        error: str,
    ) -> None:
        """按当前尝试次数计算重试时间并使用租约令牌写入。"""
        retry_at = self._retry_at(attempt_number=task.attempt_number)
        try:
            recorded = await self.repository.fail(
                task_id=task.task_id,
                lease_token=task.lease_token,
                retry_at=retry_at,
            )
        except Exception as exc:
            log_exception(
                event="napcat.image_archive.failure_record_failed",
                category="napcat_tools",
                message="写入图片归档失败状态失败，将等待租约过期",
                exc=exc,
                task_id=task.task_id,
                attempt_number=task.attempt_number,
                archive_error_type=error_type,
                archive_error=error,
            )
            return

        if not recorded:
            log_event(
                level="WARNING",
                event="napcat.image_archive.lease_lost",
                category="napcat_tools",
                message="图片归档写入失败状态时租约已丢失",
                task_id=task.task_id,
                attempt_number=task.attempt_number,
                error_type=error_type,
                error=error,
            )
            return

        log_event(
            level="WARNING",
            event=(
                "napcat.image_archive.retry_scheduled"
                if retry_at is not None
                else "napcat.image_archive.exhausted"
            ),
            category="napcat_tools",
            message=(
                "图片归档失败，已安排重试"
                if retry_at is not None
                else "图片归档失败且已用尽重试次数"
            ),
            task_id=task.task_id,
            attempt_number=task.attempt_number,
            error_type=error_type,
            error=error,
            retry_at=retry_at.isoformat() if retry_at is not None else None,
        )

    def _retry_at(self, *, attempt_number: int) -> datetime | None:
        """第一到第三次失败后延迟重试，第四次终止。"""
        retry_index = attempt_number - 1
        if retry_index >= len(self.retry_delays_seconds):
            return None
        now = self._utc_now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("utc_now 必须返回含时区的 datetime")
        return now + timedelta(seconds=self.retry_delays_seconds[retry_index])

    def _resource_for_task(
        self, *, task: ImageArchiveTask
    ) -> NapCatImageResource:
        """在服务层把纯任务 DTO 转换为 NapCat 图片读取资源。"""
        return NapCatImageResource(
            label=task.label,
            file=task.file,
            file_id=task.file_id,
            path=task.path,
            url=task.url,
        )


class ImageArchiveWorkerFactory:
    """在确定机器人身份后创建与其 NapCat 实例绑定的归档 worker。"""

    def __init__(
        self,
        *,
        repository: ImageArchiveTaskRepository,
        http_client: httpx.AsyncClient | None,
        store: ImageStore,
        concurrency: int = DEFAULT_ARCHIVE_CONCURRENCY,
        download_timeout_seconds: float = DEFAULT_ARCHIVE_READ_TIMEOUT_SECONDS,
        max_image_bytes: int = MAX_ARCHIVE_IMAGE_BYTES,
        lease_seconds: float = DEFAULT_ARCHIVE_LEASE_SECONDS,
        retry_delays_seconds: tuple[float, float, float] = (
            DEFAULT_ARCHIVE_RETRY_DELAYS_SECONDS
        ),
    ) -> None:
        """保存全局依赖，并在首个事件到来前完成配置校验。"""
        if concurrency < 1:
            raise ValueError("图片归档并发数必须大于等于 1")
        if download_timeout_seconds <= 0:
            raise ValueError("图片归档下载超时必须大于 0")
        if max_image_bytes < 1:
            raise ValueError("图片归档大小上限必须大于等于 1")
        if store.max_image_bytes != max_image_bytes:
            raise ValueError("ImageStore 与读取器的图片大小上限必须一致")
        if lease_seconds <= 0:
            raise ValueError("图片归档租约时间必须大于 0")
        if len(retry_delays_seconds) != 3:
            raise ValueError("图片归档必须配置三个重试间隔")
        if any(delay <= 0 for delay in retry_delays_seconds):
            raise ValueError("图片归档重试间隔必须全部大于 0")

        self.repository: ImageArchiveTaskRepository = repository
        self.http_client: httpx.AsyncClient | None = http_client
        self.store: ImageStore = store
        self.concurrency: int = concurrency
        self.download_timeout_seconds: float = download_timeout_seconds
        self.max_image_bytes: int = max_image_bytes
        self.lease_seconds: float = lease_seconds
        self.retry_delays_seconds: tuple[float, float, float] = (
            retry_delays_seconds
        )

    def create(
        self,
        *,
        bot_id: str,
        bot: NapCatImageBot,
    ) -> ImageArchiveWorker:
        """创建只能认领当前机器人任务的 worker。"""
        reader = NapCatImageReader(
            bot=bot,
            http_client=self.http_client,
            fetch_concurrency=self.concurrency,
            download_timeout_seconds=self.download_timeout_seconds,
            max_image_bytes=self.max_image_bytes,
        )
        return ImageArchiveWorker(
            bot_id=bot_id,
            repository=self.repository,
            reader=reader,
            store=self.store,
            concurrency=self.concurrency,
            read_timeout_seconds=self.download_timeout_seconds,
            lease_seconds=self.lease_seconds,
            retry_delays_seconds=self.retry_delays_seconds,
        )


__all__ = [
    "DEFAULT_ARCHIVE_CONCURRENCY",
    "DEFAULT_ARCHIVE_LEASE_SECONDS",
    "DEFAULT_ARCHIVE_POLL_INTERVAL_SECONDS",
    "DEFAULT_ARCHIVE_READ_TIMEOUT_SECONDS",
    "DEFAULT_ARCHIVE_RETRY_DELAYS_SECONDS",
    "ImageArchiveError",
    "ImageArchiveReader",
    "ImageArchiveTask",
    "ImageArchiveTaskRepository",
    "ImageArchiveWorker",
    "ImageArchiveWorkerFactory",
    "ImageStore",
    "ImageTooLargeError",
    "InvalidImageContentError",
    "InvalidInlineImageSourceError",
    "InlineImageArchiveResult",
    "InlineImageArchiver",
    "MAX_ARCHIVE_ATTEMPTS",
    "MAX_ARCHIVE_IMAGE_BYTES",
    "StoredImage",
]
