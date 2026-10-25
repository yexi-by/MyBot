"""NapCat 共用图片读取服务测试。"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx

from app.models import Response
from app.services.napcat.image_reader import (
    NapCatImageBot,
    NapCatImageReader as RealNapCatImageReader,
    NapCatImageResource,
)


def NapCatImageReader(
    *,
    bot: object,
    http_client: httpx.AsyncClient | None,
    fetch_concurrency: int,
    download_timeout_seconds: float,
    max_image_bytes: int | None = None,
) -> RealNapCatImageReader:
    """用显式的不限大小策略构造测试读取器。"""
    return RealNapCatImageReader(
        bot=cast(NapCatImageBot, bot),
        http_client=http_client,
        fetch_concurrency=fetch_concurrency,
        download_timeout_seconds=download_timeout_seconds,
        max_image_bytes=max_image_bytes,
    )


class FakeImageBot:
    """按 file 或 file_id 返回预置图片刷新结果。"""

    def __init__(self) -> None:
        """初始化调用记录和响应表。"""
        self.calls: list[tuple[str | None, str | None]] = []
        self.responses: dict[str, Response] = {}

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """返回预置结果。"""
        self.calls.append((file_id, file))
        key = file if file is not None else file_id
        if key is not None and key in self.responses:
            return self.responses[key]
        return Response(status="failed", retcode=404, message="图片不存在")


class TimeoutHTTPClient:
    """模拟 URL 下载超时。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[str, float]] = []

    async def get(self, url: str, timeout: float) -> httpx.Response:
        """记录超时参数并抛出读取超时。"""
        self.calls.append((url, timeout))
        raise httpx.ReadTimeout("读取超时")


class ConcurrencyHTTPClient:
    """记录同时进行的 URL 下载数量。"""

    def __init__(self) -> None:
        """初始化并发计数。"""
        self.active = 0
        self.max_active = 0

    async def get(self, url: str, timeout: float) -> httpx.Response:
        """短暂让出执行权以观察信号量限制。"""
        _ = timeout
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return httpx.Response(
            status_code=200,
            content=url.encode(),
            request=httpx.Request("GET", url),
        )


class NapCatImageReaderTest(unittest.IsolatedAsyncioTestCase):
    """验证读取顺序、超时、并发和部分失败。"""

    async def test_local_path_precedes_existing_url_and_napcat(self) -> None:
        """本地文件存在时不请求 URL，也不刷新 NapCat。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "image.bin"
            path.write_bytes(b"local-image")
            bot = FakeImageBot()
            timeout_client = TimeoutHTTPClient()
            reader = NapCatImageReader(
                bot=bot,
                http_client=cast(httpx.AsyncClient, timeout_client),
                fetch_concurrency=2,
                download_timeout_seconds=3.0,
            )

            result = await reader.read(
                resource=NapCatImageResource(
                    label="当前消息第 1 张图片",
                    path=str(path),
                    url="https://example.com/unused.png",
                    file="unused.png",
                )
            )

        self.assertEqual(result.image_bytes, b"local-image")
        self.assertEqual(result.source, "direct_path")
        self.assertEqual(timeout_client.calls, [])
        self.assertEqual(bot.calls, [])

    async def test_existing_url_precedes_napcat_refresh(self) -> None:
        """现有 URL 成功时不触发 NapCat get_image。"""
        bot = FakeImageBot()

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"url-image", request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            reader = NapCatImageReader(
                bot=bot,
                http_client=http_client,
                fetch_concurrency=2,
                download_timeout_seconds=3.0,
            )
            result = await reader.read(
                resource=NapCatImageResource(
                    label="当前消息第 1 张图片",
                    url="https://example.com/image.png",
                    file="unused.png",
                )
            )

        self.assertEqual(result.image_bytes, b"url-image")
        self.assertEqual(result.source, "direct_url")
        self.assertEqual(bot.calls, [])

    async def test_failed_direct_sources_fall_back_to_napcat_base64(self) -> None:
        """本地路径和 URL 都失败后使用 NapCat 刷新的 base64。"""
        bot = FakeImageBot()
        bot.responses["image.png"] = Response(
            status="ok",
            retcode=0,
            data={"base64": "cmVmcmVzaGVkLWltYWdl"},
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            reader = NapCatImageReader(
                bot=bot,
                http_client=http_client,
                fetch_concurrency=2,
                download_timeout_seconds=3.0,
            )
            result = await reader.read(
                resource=NapCatImageResource(
                    label="引用消息第 1 张图片",
                    path="missing-image.bin",
                    url="https://example.com/expired.png",
                    file="image.png",
                )
            )

        self.assertEqual(result.image_bytes, b"refreshed-image")
        self.assertEqual(result.source, "napcat_refresh")
        self.assertEqual(bot.calls, [(None, "image.png")])

    async def test_oversized_non_aligned_base64_is_rejected_before_decode(
        self,
    ) -> None:
        """长度不是 4 的倍数也必须在解码分配内存前按上限拒绝。"""
        bot = FakeImageBot()
        bot.responses["oversized.png"] = Response(
            status="ok",
            retcode=0,
            data={"base64": "A" * 9},
        )
        reader = NapCatImageReader(
            bot=bot,
            http_client=None,
            fetch_concurrency=1,
            download_timeout_seconds=3.0,
            max_image_bytes=4,
        )

        with patch(
            "app.services.napcat.image_reader.base64.b64decode"
        ) as decode:
            result = await reader.read(
                resource=NapCatImageResource(
                    label="超限图片",
                    file="oversized.png",
                )
            )

        decode.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "ImageReadTooLargeError")
        self.assertIn("base64 长度超过", result.error or "")

    async def test_base64_size_precheck_allows_legal_padding(self) -> None:
        """编码长度恰好达到上限时不误拒绝合法 padding。"""
        bot = FakeImageBot()
        bot.responses["padded.png"] = Response(
            status="ok",
            retcode=0,
            data={"base64": "YQ=="},
        )
        reader = NapCatImageReader(
            bot=bot,
            http_client=None,
            fetch_concurrency=1,
            download_timeout_seconds=3.0,
            max_image_bytes=1,
        )

        result = await reader.read(
            resource=NapCatImageResource(
                label="合法 padding 图片",
                file="padded.png",
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.image_bytes, b"a")
        self.assertEqual(result.source, "napcat_refresh")

    async def test_read_many_preserves_order_and_returns_partial_errors(self) -> None:
        """批量读取保持输入顺序，单张失败不会中断其他图片。"""
        bot = FakeImageBot()
        bot.responses["good.png"] = Response(
            status="ok",
            retcode=0,
            data={"base64": "Z29vZA=="},
        )
        reader = NapCatImageReader(
            bot=bot,
            http_client=None,
            fetch_concurrency=2,
            download_timeout_seconds=3.0,
        )

        results = await reader.read_many(
            resources=[
                NapCatImageResource(label="第一张", file="good.png"),
                NapCatImageResource(label="第二张", file="missing.png"),
            ]
        )

        self.assertEqual(
            [result.resource.label for result in results],
            ["第一张", "第二张"],
        )
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)
        self.assertEqual(results[1].error_type, "NapCatActionFailed")

    async def test_read_many_requires_explicit_concurrency(self) -> None:
        """仅配置为单图读取的实例不能静默采用批量并发数。"""
        reader = RealNapCatImageReader(
            bot=cast(NapCatImageBot, FakeImageBot()),
            http_client=None,
            fetch_concurrency=None,
            download_timeout_seconds=3.0,
            max_image_bytes=None,
        )

        with self.assertRaisesRegex(RuntimeError, "fetch_concurrency"):
            _ = await reader.read_many(resources=[])

    async def test_concurrency_limit_applies_to_shared_reader(self) -> None:
        """批量 URL 下载不会超过配置的并发数。"""
        client = ConcurrencyHTTPClient()
        reader = NapCatImageReader(
            bot=FakeImageBot(),
            http_client=cast(httpx.AsyncClient, client),
            fetch_concurrency=2,
            download_timeout_seconds=3.0,
        )
        resources = [
            NapCatImageResource(
                label=f"图片 {index}",
                url=f"https://example.com/{index}.png",
            )
            for index in range(5)
        ]

        results = await reader.read_many(resources=resources)

        self.assertEqual(client.max_active, 2)
        self.assertEqual(
            [result.image_bytes for result in results],
            [
                b"https://example.com/0.png",
                b"https://example.com/1.png",
                b"https://example.com/2.png",
                b"https://example.com/3.png",
                b"https://example.com/4.png",
            ],
        )

    async def test_download_timeout_is_returned_as_recoverable_error(self) -> None:
        """下载超时会保留在单张图片错误中。"""
        client = TimeoutHTTPClient()
        reader = NapCatImageReader(
            bot=FakeImageBot(),
            http_client=cast(httpx.AsyncClient, client),
            fetch_concurrency=1,
            download_timeout_seconds=0.25,
        )

        result = await reader.read(
            resource=NapCatImageResource(
                label="当前消息第 1 张图片",
                url="https://example.com/slow.png",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "ReadTimeout")
        self.assertEqual(
            client.calls,
            [("https://example.com/slow.png", 0.25)],
        )
        self.assertIn("读取超时", result.error or "")

    async def test_hanging_refresh_returns_partial_error(self) -> None:
        """NapCat 刷新挂起时按单阶段预算返回错误，其他图片正常完成。"""
        class HangingBot:
            async def get_image(
                self, file_id: str | None = None, file: str | None = None,
            ) -> Response:
                if file == "slow.png":
                    await asyncio.Event().wait()
                return Response(status="ok", retcode=0, data={"base64": "YQ=="})

        reader = NapCatImageReader(
            bot=HangingBot(), http_client=None, fetch_concurrency=2,
            download_timeout_seconds=0.05,
        )
        results = await reader.read_many(resources=[
            NapCatImageResource(label="超时图片", file="slow.png"),
            NapCatImageResource(label="正常图片", file="good.png"),
        ])
        self.assertEqual(results[0].error_type, "TimeoutError")
        self.assertIn("NapCat 刷新图片信息失败: TimeoutError", results[0].error or "")
        self.assertEqual(results[1].image_bytes, b"a")

    async def test_refreshed_download_reports_latest_error_and_connection_cause(self) -> None:
        """最终错误类型跟随刷新后的失败，同时保留首次连接失败的底层原因。"""
        bot = FakeImageBot()
        bot.responses["image.png"] = Response(status="ok", retcode=0, data={
            "url": "https://example.com/refreshed.png",
        })

        async def download(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/refreshed.png":
                raise httpx.ReadTimeout("")
            try:
                raise OSError("connection reset by peer")
            except OSError as exc:
                raise httpx.ConnectError("") from exc

        async with httpx.AsyncClient(transport=httpx.MockTransport(download)) as client:
            reader = NapCatImageReader(
                bot=bot, http_client=client, fetch_concurrency=1,
                download_timeout_seconds=0.1,
            )
            result = await reader.read(resource=NapCatImageResource(
                label="图片", file="image.png", url="https://example.com/original.png",
            ))
        self.assertEqual(result.error_type, "ReadTimeout")
        self.assertIn("ConnectError <- OSError: connection reset by peer", result.error or "")
        self.assertIn("下载 NapCat 刷新 URL 失败: ReadTimeout", result.error or "")


if __name__ == "__main__":
    unittest.main()
