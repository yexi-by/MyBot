"""NapCat 图片内容寻址归档和可恢复 worker 测试。"""

import asyncio
import base64
import hashlib
import tempfile
import unittest
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx

from app.models import ImageArchiveTask, Response, StoredImage
from app.services.napcat.image_archive import (
    ImageArchiveReader,
    ImageArchiveTaskRepository,
    ImageArchiveWorker as RealImageArchiveWorker,
    ImageArchiveWorkerFactory as RealImageArchiveWorkerFactory,
    ImageStore as RealImageStore,
    ImageTooLargeError,
    InlineImageArchiver,
    InvalidImageContentError,
    InvalidInlineImageSourceError,
)
from app.services.napcat.image_reader import (
    NapCatImageReader,
    NapCatImageReadResult,
    NapCatImageResource,
)

TEST_MAX_IMAGE_BYTES = 50 * 1024 * 1024
TEST_CONCURRENCY = 16
TEST_READ_TIMEOUT_SECONDS = 20.0
TEST_LEASE_SECONDS = 45.0
TEST_POLL_INTERVAL_SECONDS = 1.0
TEST_RETRY_DELAYS_SECONDS = (1.0, 5.0, 20.0)


def ImageStore(
    *, root: Path, max_image_bytes: int = TEST_MAX_IMAGE_BYTES
) -> RealImageStore:
    """用显式测试上限构造图片存储。"""
    return RealImageStore(root=root, max_image_bytes=max_image_bytes)


def ImageArchiveWorker(
    *,
    bot_id: str,
    repository: object,
    reader: object,
    store: RealImageStore,
    concurrency: int = TEST_CONCURRENCY,
    read_timeout_seconds: float = TEST_READ_TIMEOUT_SECONDS,
    lease_seconds: float = TEST_LEASE_SECONDS,
    poll_interval_seconds: float = TEST_POLL_INTERVAL_SECONDS,
    retry_delays_seconds: tuple[float, ...] = TEST_RETRY_DELAYS_SECONDS,
    utc_now: Callable[[], datetime] | None = None,
) -> RealImageArchiveWorker:
    """用显式测试运行参数构造归档 worker。"""
    return RealImageArchiveWorker(
        bot_id=bot_id,
        repository=cast(ImageArchiveTaskRepository, repository),
        reader=cast(ImageArchiveReader, reader),
        store=store,
        concurrency=concurrency,
        read_timeout_seconds=read_timeout_seconds,
        lease_seconds=lease_seconds,
        poll_interval_seconds=poll_interval_seconds,
        retry_delays_seconds=retry_delays_seconds,
        utc_now=utc_now,
    )


def ImageArchiveWorkerFactory(
    *,
    repository: object,
    http_client: httpx.AsyncClient | None,
    store: RealImageStore,
    concurrency: int = TEST_CONCURRENCY,
    download_timeout_seconds: float = TEST_READ_TIMEOUT_SECONDS,
    max_image_bytes: int = TEST_MAX_IMAGE_BYTES,
    lease_seconds: float = TEST_LEASE_SECONDS,
    poll_interval_seconds: float = TEST_POLL_INTERVAL_SECONDS,
    retry_delays_seconds: tuple[float, ...] = TEST_RETRY_DELAYS_SECONDS,
) -> RealImageArchiveWorkerFactory:
    """用显式测试运行参数构造归档 worker 工厂。"""
    return RealImageArchiveWorkerFactory(
        repository=cast(ImageArchiveTaskRepository, repository),
        http_client=http_client,
        store=store,
        concurrency=concurrency,
        download_timeout_seconds=download_timeout_seconds,
        max_image_bytes=max_image_bytes,
        lease_seconds=lease_seconds,
        poll_interval_seconds=poll_interval_seconds,
        retry_delays_seconds=retry_delays_seconds,
    )

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
    b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
)
GIF_BYTES = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"


class FakeArchiveRepository:
    """记录 worker 与租约仓库之间的调用。"""

    def __init__(self, *, tasks: Sequence[ImageArchiveTask]) -> None:
        """初始化待认领任务和调用记录。"""
        self.tasks: list[ImageArchiveTask] = list(tasks)
        self.claim_calls: list[tuple[str, int, float]] = []
        self.complete_calls: list[tuple[int, str, StoredImage]] = []
        self.fail_calls: list[tuple[int, str, datetime | None]] = []

    async def claim_ready(
        self,
        *,
        bot_id: str,
        limit: int,
        lease_seconds: float,
    ) -> Sequence[ImageArchiveTask]:
        """一次返回所有预置任务。"""
        self.claim_calls.append((bot_id, limit, lease_seconds))
        claimed = self.tasks
        self.tasks = []
        return claimed

    async def complete(
        self,
        *,
        task_id: int,
        lease_token: str,
        image: StoredImage,
    ) -> bool:
        """记录成功完成的任务。"""
        self.complete_calls.append((task_id, lease_token, image))
        return True

    async def fail(
        self,
        *,
        task_id: int,
        lease_token: str,
        retry_at: datetime | None,
    ) -> bool:
        """记录失败任务、租约和重试时间。"""
        self.fail_calls.append((task_id, lease_token, retry_at))
        return True


class SuccessfulReader:
    """为每个任务返回有效图片。"""

    async def read(
        self, *, resource: NapCatImageResource
    ) -> NapCatImageReadResult:
        """返回成功的 URL 读取结果。"""
        return NapCatImageReadResult(
            resource=resource,
            image_bytes=PNG_BYTES,
            source="direct_url",
            error_type=None,
            error=None,
        )


class FailedReader:
    """为每个任务返回可恢复失败。"""

    async def read(
        self, *, resource: NapCatImageResource
    ) -> NapCatImageReadResult:
        """返回图片暂时不可用。"""
        return NapCatImageReadResult(
            resource=resource,
            image_bytes=None,
            source=None,
            error_type="ReadTimeout",
            error="读取超时",
        )


class BlockingReader:
    """记录读取阶段的实际并发数。"""

    def __init__(self, *, sleep_seconds: float = 0.01) -> None:
        """初始化延迟和并发计数。"""
        self.sleep_seconds = sleep_seconds
        self.active = 0
        self.max_active = 0

    async def read(
        self, *, resource: NapCatImageResource
    ) -> NapCatImageReadResult:
        """短暂挂起以暴露 worker 并发上限。"""
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.sleep_seconds)
        finally:
            self.active -= 1
        suffix = int(resource.label.rsplit(" ", maxsplit=1)[-1])
        return NapCatImageReadResult(
            resource=resource,
            image_bytes=GIF_BYTES + bytes([suffix]),
            source="direct_path",
            error_type=None,
            error=None,
        )


class FakeImageBot:
    """工厂测试中与指定 bot_id 绑定的 NapCat 实例。"""

    async def get_image(
        self,
        file_id: str | None = None,
        file: str | None = None,
    ) -> Response:
        """工厂创建不会立即读图，仍提供完整协议。"""
        _ = (file_id, file)
        return Response(status="failed", retcode=404, message="图片不存在")


class ImageStoreTest(unittest.IsolatedAsyncioTestCase):
    """验证图片真实类型、大小限制和内容去重。"""

    async def test_valid_image_is_stored_by_sha256_and_deduplicated(self) -> None:
        """同一内容只对应一个相对存储键。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ImageStore(root=root)

            first, second = await asyncio.gather(
                store.store(image_bytes=PNG_BYTES),
                store.store(image_bytes=PNG_BYTES),
            )

            digest = hashlib.sha256(PNG_BYTES).hexdigest()
            expected_key = f"{digest[:2]}/{digest[2:4]}/{digest}.png"
            stored_files = [path for path in root.rglob("*") if path.is_file()]

            self.assertEqual(first, second)
            self.assertEqual(first.storage_key, expected_key)
            self.assertEqual(first.mime_type, "image/png")
            self.assertEqual(first.size_bytes, len(PNG_BYTES))
            self.assertFalse(Path(first.storage_key).is_absolute())
            self.assertEqual(stored_files, [root / Path(expected_key)])
            self.assertEqual(stored_files[0].read_bytes(), PNG_BYTES)
            self.assertEqual(list(root.rglob("*.tmp")), [])

    async def test_size_limit_is_enforced_before_type_detection(self) -> None:
        """超限内容不会创建目录或文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "images"
            store = ImageStore(root=root, max_image_bytes=4)

            with self.assertRaises(ImageTooLargeError):
                await store.store(image_bytes=PNG_BYTES)

            self.assertFalse(root.exists())

    async def test_non_image_content_is_rejected(self) -> None:
        """不信任文件名或调用方声称的 MIME。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "images"
            store = ImageStore(root=root)

            with self.assertRaises(InvalidImageContentError):
                await store.store(image_bytes=b"not an image")

            self.assertFalse(root.exists())

class InlineImageArchiverTest(unittest.IsolatedAsyncioTestCase):
    """验证出站内联图片不依赖 NapCat echo 也能永久归档。"""

    async def test_base64_and_data_url_share_one_archived_file(self) -> None:
        """两种合法内联形式返回同一实际绝对路径。"""
        encoded = base64.b64encode(PNG_BYTES).decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "images"
            archiver = InlineImageArchiver(store=ImageStore(root=root))

            base64_result, data_url_result = await asyncio.gather(
                archiver.archive(source=f"BASE64://{encoded}"),
                archiver.archive(source=f"DATA:IMAGE/PNG;BASE64,{encoded}"),
            )

            stored_files = [path for path in root.rglob("*") if path.is_file()]
            self.assertEqual(
                base64_result.absolute_path,
                data_url_result.absolute_path,
            )
            self.assertTrue(base64_result.absolute_path.is_absolute())
            self.assertTrue(
                base64_result.absolute_path.is_relative_to(root.resolve())
            )
            self.assertEqual(base64_result.absolute_path.read_bytes(), PNG_BYTES)
            self.assertEqual(stored_files, [base64_result.absolute_path])

    async def test_invalid_base64_is_rejected_without_writing(self) -> None:
        """非法 base64 不会产生任何图片文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "images"
            archiver = InlineImageArchiver(store=ImageStore(root=root))

            with self.assertRaises(InvalidInlineImageSourceError):
                await archiver.archive(source="base64://%%%%")

            self.assertFalse(root.exists())

    async def test_data_url_must_declare_image_mime(self) -> None:
        """即使负载是图片，非图片 data URL 也不能进入归档。"""
        encoded = base64.b64encode(PNG_BYTES).decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "images"
            archiver = InlineImageArchiver(store=ImageStore(root=root))

            with self.assertRaises(InvalidInlineImageSourceError):
                await archiver.archive(
                    source=f"data:text/plain;base64,{encoded}"
                )

            self.assertFalse(root.exists())

    async def test_oversized_payload_is_rejected_before_writing(self) -> None:
        """解码结果超过存储上限时不创建文件。"""
        encoded = base64.b64encode(PNG_BYTES).decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "images"
            archiver = InlineImageArchiver(
                store=ImageStore(
                    root=root,
                    max_image_bytes=len(PNG_BYTES) - 1,
                )
            )

            with self.assertRaises(ImageTooLargeError):
                await archiver.archive(source=f"base64://{encoded}")

            self.assertFalse(root.exists())


class ImageReaderSizeLimitTest(unittest.IsolatedAsyncioTestCase):
    """验证归档和其他调用方可在共用 reader 处限制图片大小。"""

    async def test_streaming_url_stops_when_content_length_exceeds_limit(
        self,
    ) -> None:
        """URL 声明大小超限时返回可恢复错误。"""

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"content-length": str(len(PNG_BYTES))},
                request=request,
            )

        class UnusedBot:
            """无图片标识时不会调用的 NapCat 能力。"""

            async def get_image(
                self,
                file_id: str | None = None,
                file: str | None = None,
            ) -> Response:
                """如果意外调用则立即失败。"""
                _ = (file_id, file)
                raise AssertionError("不应调用 NapCat 刷新")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            reader = NapCatImageReader(
                bot=UnusedBot(),
                http_client=http_client,
                fetch_concurrency=1,
                download_timeout_seconds=30.0,
                max_image_bytes=4,
            )
            result = await reader.read(
                resource=NapCatImageResource(
                    label="超限图片",
                    url="https://example.com/large.png",
                )
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "ImageReadTooLargeError")
        self.assertIn("超过上限", result.error or "")


class ImageArchiveWorkerTest(unittest.IsolatedAsyncioTestCase):
    """验证 worker 的租约、并发、超时和四次尝试。"""

    def _task(self, *, task_id: int, attempt_number: int = 1) -> ImageArchiveTask:
        """构造不含任何视频字段的图片任务。"""
        return ImageArchiveTask(
            task_id=task_id,
            lease_token=f"unpredictable-token-{task_id}",
            attempt_number=attempt_number,
            label=f"图片 {task_id}",
            file=f"image-{task_id}.png",
            file_id=f"file-id-{task_id}",
            path=f"missing-{task_id}.png",
            url=f"https://example.com/{task_id}.png",
        )

    async def test_success_uses_default_claim_boundaries_and_completes(self) -> None:
        """默认认领十六张、20 秒读取超时和 45 秒租约。"""
        repository = FakeArchiveRepository(tasks=[self._task(task_id=1)])
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = ImageArchiveWorker(
                bot_id="bot-10001",
                repository=repository,
                reader=SuccessfulReader(),
                store=ImageStore(root=Path(temp_dir)),
            )

            processed = await worker.run_once()

        self.assertEqual(repository.claim_calls, [("bot-10001", 16, 45.0)])
        self.assertEqual(processed, 1)
        self.assertEqual(repository.fail_calls, [])
        self.assertEqual(len(repository.complete_calls), 1)
        task_id, token, stored = repository.complete_calls[0]
        self.assertEqual((task_id, token), (1, "unpredictable-token-1"))
        self.assertEqual(stored.mime_type, "image/png")

    async def test_factory_binds_bot_and_all_archive_limits(self) -> None:
        """工厂在确定 bot_id 后才创建带限量 reader 的 worker。"""
        repository = FakeArchiveRepository(tasks=[])
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ImageStore(root=Path(temp_dir), max_image_bytes=1024)
            factory = ImageArchiveWorkerFactory(
                repository=repository,
                http_client=None,
                store=store,
                concurrency=3,
                download_timeout_seconds=12.0,
                max_image_bytes=1024,
                lease_seconds=45.0,
                retry_delays_seconds=(1.0, 2.0, 3.0),
            )

            worker = factory.create(bot_id="bot-A", bot=FakeImageBot())
            processed = await worker.run_once()

        self.assertEqual(processed, 0)
        self.assertEqual(worker.bot_id, "bot-A")
        self.assertEqual(worker.concurrency, 3)
        self.assertEqual(worker.read_timeout_seconds, 36.0)
        self.assertEqual(worker.lease_seconds, 81.0)
        self.assertEqual(worker.retry_delays_seconds, (1.0, 2.0, 3.0))
        self.assertEqual(repository.claim_calls, [("bot-A", 3, 81.0)])
        self.assertIsInstance(worker.reader, NapCatImageReader)
        reader = worker.reader
        if not isinstance(reader, NapCatImageReader):
            self.fail("工厂必须创建 NapCatImageReader")
        self.assertEqual(reader.max_image_bytes, 1024)
        self.assertEqual(reader.download_timeout_seconds, 12.0)

    async def test_direct_download_timeout_still_allows_refresh_and_archive(self) -> None:
        """首次下载耗尽预算后，刷新和新 URL 下载仍能完成归档。"""
        class RefreshBot:
            async def get_image(
                self, file_id: str | None = None, file: str | None = None,
            ) -> Response:
                await asyncio.sleep(0.01)
                return Response(status="ok", retcode=0, data={
                    "file": "/missing-napcat-cache/image.png",
                    "url": "https://example.com/refreshed.png",
                })

        async def download(request: httpx.Request) -> httpx.Response:
            if request.url.path != "/refreshed.png":
                await asyncio.Event().wait()
            return httpx.Response(200, content=PNG_BYTES)

        repository = FakeArchiveRepository(tasks=[self._task(task_id=1)])
        with tempfile.TemporaryDirectory() as temp_dir:
            async with httpx.AsyncClient(transport=httpx.MockTransport(download)) as client:
                factory = ImageArchiveWorkerFactory(
                    repository=repository, http_client=client,
                    store=ImageStore(root=Path(temp_dir)),
                    download_timeout_seconds=0.1,
                )
                worker = factory.create(bot_id="bot-A", bot=RefreshBot())
                await worker.run_once()

        self.assertEqual(repository.fail_calls, [])
        self.assertEqual(len(repository.complete_calls), 1)
        self.assertEqual(repository.complete_calls[0][2].size_bytes, len(PNG_BYTES))

    async def test_factory_rejects_mismatched_store_limit(self) -> None:
        """读取上限和最终存储上限不能分叉。"""
        repository = FakeArchiveRepository(tasks=[])
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ImageStore(root=Path(temp_dir), max_image_bytes=1024)

            with self.assertRaisesRegex(ValueError, "大小上限必须一致"):
                _ = ImageArchiveWorkerFactory(
                    repository=repository,
                    http_client=None,
                    store=store,
                    max_image_bytes=2048,
                )

    async def test_failures_retry_after_one_five_and_twenty_seconds(self) -> None:
        """前三次失败延迟重试，第四次进入终态。"""
        frozen_now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
        repository = FakeArchiveRepository(
            tasks=[
                self._task(task_id=index, attempt_number=index)
                for index in range(1, 5)
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = ImageArchiveWorker(
                bot_id="bot-10001",
                repository=repository,
                reader=FailedReader(),
                store=ImageStore(root=Path(temp_dir)),
                utc_now=lambda: frozen_now,
            )

            await worker.run_once()

        self.assertEqual(repository.complete_calls, [])
        self.assertEqual(
            [call[2] for call in repository.fail_calls],
            [
                datetime(2026, 8, 16, 12, 0, 1, tzinfo=UTC),
                datetime(2026, 8, 16, 12, 0, 5, tzinfo=UTC),
                datetime(2026, 8, 16, 12, 0, 20, tzinfo=UTC),
                None,
            ],
        )

    async def test_whole_read_is_limited_by_timeout(self) -> None:
        """即使抽象 reader 自身不限时，worker 也会终止本次读取。"""
        repository = FakeArchiveRepository(tasks=[self._task(task_id=1)])
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = ImageArchiveWorker(
                bot_id="bot-10001",
                repository=repository,
                reader=BlockingReader(sleep_seconds=1.0),
                store=ImageStore(root=Path(temp_dir)),
                read_timeout_seconds=0.01,
            )

            await worker.run_once()

        self.assertEqual(repository.complete_calls, [])
        self.assertEqual(len(repository.fail_calls), 1)

    async def test_worker_never_exceeds_configured_concurrency(self) -> None:
        """即使仓库违反 limit 返回过多任务，worker 也不超过十六个并发。"""
        repository = FakeArchiveRepository(
            tasks=[self._task(task_id=index) for index in range(1, 25)]
        )
        reader = BlockingReader()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = ImageArchiveWorker(
                bot_id="bot-10001",
                repository=repository,
                reader=reader,
                store=ImageStore(root=Path(temp_dir)),
            )

            processed = await worker.run_once()

        self.assertEqual(processed, 24)
        self.assertEqual(reader.max_active, 16)
        self.assertEqual(len(repository.complete_calls), 24)
        self.assertEqual(repository.fail_calls, [])


if __name__ == "__main__":
    unittest.main()
