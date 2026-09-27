"""NapCat 视频资源读取，优先本地路径与消息段已有 URL。"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import aiofiles
import httpx

from app.models import Response, to_json_value
from app.services.napcat.image_reader import NapCatImageBot
from app.utils.file_type import detect_mime_type
from app.utils.log import log_event


class NapCatVideoBot(Protocol):
    """刷新视频文件来源所需的 NapCat 接口。"""

    async def get_file(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response: ...


class NapCatMediaBot(NapCatImageBot, NapCatVideoBot, Protocol):
    """读取图片与视频所需的 NapCat 接口。"""


@dataclass(frozen=True)
class NapCatVideoResource:
    """来自当前消息或引用消息的视频。"""

    label: str
    file: str
    file_id: str | None = None
    path: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class NapCatVideoReadResult:
    """单个视频的内容或可恢复错误。"""

    resource: NapCatVideoResource
    video_bytes: bytes | None
    source: Literal["direct_path", "direct_url", "napcat_refresh"] | None
    error_type: str | None = None
    error: str | None = None


class VideoReadTooLargeError(ValueError):
    """视频超过调用方设置的原始字节上限。"""


class NapCatVideoReader:
    """有界并发读取视频，单个失败保留为结构化结果。"""

    def __init__(
        self,
        *,
        bot: NapCatVideoBot,
        http_client: httpx.AsyncClient | None,
        fetch_concurrency: int,
        download_timeout_seconds: float,
        max_video_bytes: int,
    ) -> None:
        self.bot = bot
        self.http_client = http_client
        self.fetch_concurrency = fetch_concurrency
        self.download_timeout_seconds = download_timeout_seconds
        self.max_video_bytes = max_video_bytes

    async def read_many(
        self, *, resources: list[NapCatVideoResource]
    ) -> list[NapCatVideoReadResult]:
        """并发读取并保留消息顺序。"""
        semaphore = asyncio.Semaphore(self.fetch_concurrency)

        async def read_one(resource: NapCatVideoResource) -> NapCatVideoReadResult:
            async with semaphore:
                return await self.read(resource=resource)

        return list(await asyncio.gather(*(read_one(item) for item in resources)))

    async def read(self, *, resource: NapCatVideoResource) -> NapCatVideoReadResult:
        """已有来源均失败时才请求 get_file 刷新；超限直接返回错误。"""
        failures: list[str] = []
        source: Literal["direct_path", "direct_url", "napcat_refresh"] | None = None
        try:
            url = resource.url
            if not url and resource.file.startswith(("http://", "https://")):
                url = resource.file
            direct = await self._read_sources(
                paths=[resource.path, resource.file],
                url=url,
                failures=failures,
            )
            if direct is not None:
                content, source = direct
            else:
                if not (resource.file_id or resource.file):
                    raise ValueError("视频段缺少可用于 NapCat 刷新的 file 或 file_id")
                async with asyncio.timeout(self.download_timeout_seconds):
                    response = await self.bot.get_file(
                        file_id=resource.file_id,
                        file=None if resource.file_id else resource.file,
                    )
                if response.status != "ok" or response.retcode != 0:
                    raise ValueError("NapCat get_file 刷新视频信息失败")
                data = response.data if isinstance(response.data, dict) else {}
                refreshed = await self._read_sources(
                    paths=[data.get("path"), data.get("file")],
                    url=data.get("url"),
                    failures=failures,
                )
                if refreshed is None:
                    raise ValueError("NapCat 未返回可读取的视频来源")
                content, _ = refreshed
                source = "napcat_refresh"
            result = NapCatVideoReadResult(resource, content, source)
        except (OSError, httpx.HTTPError, ValueError, TimeoutError) as exc:
            failures.append(self._error_detail(exc))
            result = NapCatVideoReadResult(
                resource, None, source, type(exc).__name__, "；".join(failures)
            )
        log_event(
            level="DEBUG" if result.video_bytes is not None else "WARNING",
            event="napcat.video_reader.finished",
            category="napcat_tools",
            message="NapCat 视频资源读取完成",
            resource_type="video",
            label=resource.label,
            path=resource.path,
            has_url=bool(resource.url),
            source=result.source,
            ok=result.video_bytes is not None,
            bytes_count=len(result.video_bytes or b""),
            error_type=result.error_type,
            error=result.error,
            source_failures=to_json_value(failures),
        )
        return result

    async def _read_sources(
        self, *, paths: list[object], url: object, failures: list[str]
    ) -> tuple[bytes, Literal["direct_path", "direct_url"]] | None:
        """本地文件可读时直接使用，否则下载已有 URL。"""
        for value in dict.fromkeys(item for item in paths if isinstance(item, str)):
            if not value or value.startswith(("http://", "https://")):
                continue
            path = Path(value)
            try:
                async with asyncio.timeout(self.download_timeout_seconds):
                    if not await asyncio.to_thread(path.is_file):
                        failures.append(f"本地视频路径不可用: {path}")
                        continue
                    size = (await asyncio.to_thread(path.stat)).st_size
                    self._check_size(size)
                    async with aiofiles.open(path, "rb") as file:
                        content = await file.read(self.max_video_bytes + 1 if self.max_video_bytes else -1)
                    self._check_content(content)
                    return content, "direct_path"
            except VideoReadTooLargeError:
                raise
            except (OSError, ValueError, TimeoutError) as exc:
                failures.append(f"本地视频读取失败: {self._error_detail(exc)}")
        if isinstance(url, str) and url.strip():
            try:
                async with asyncio.timeout(self.download_timeout_seconds):
                    content = await self._download(url)
                    self._check_content(content)
                    return content, "direct_url"
            except VideoReadTooLargeError:
                raise
            except (httpx.HTTPError, ValueError, TimeoutError) as exc:
                failures.append(f"视频 URL 下载失败: {self._error_detail(exc)}")
        return None

    async def _download(self, url: str) -> bytes:
        """流式下载，在分配完整文件内存前检查长度。"""
        if urlsplit(url).scheme not in {"http", "https"}:
            raise ValueError("视频 URL 必须使用 HTTP 或 HTTPS")
        if self.http_client is None:
            raise RuntimeError("视频 URL 下载需要 HTTP 客户端")
        async with self.http_client.stream(
            "GET", url, timeout=self.download_timeout_seconds, follow_redirects=True
        ) as response:
            response.raise_for_status()
            declared_size = response.headers.get("content-length", "")
            if declared_size.isdecimal():
                self._check_size(int(declared_size))
            content = bytearray()
            async for chunk in response.aiter_bytes():
                self._check_size(len(content) + len(chunk))
                content.extend(chunk)
            return bytes(content)

    def _check_size(self, size: int) -> None:
        if self.max_video_bytes and size > self.max_video_bytes:
            raise VideoReadTooLargeError(
                f"视频大小 {size} 字节超过上限 {self.max_video_bytes} 字节"
            )

    def _check_content(self, content: bytes) -> None:
        self._check_size(len(content))
        if not detect_mime_type(content).startswith("video/"):
            raise ValueError("读取的文件不是支持的视频格式")

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        """保留错误原因，HTTP 异常只展示状态以避免泄漏签名 URL。"""
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTPStatusError: HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.RequestError):
            return type(exc).__name__
        return f"{type(exc).__name__}: {exc}".rstrip(": ")
