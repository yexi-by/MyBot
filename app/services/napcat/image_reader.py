"""NapCat 图片资源读取服务。"""

import asyncio
import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import aiofiles
import httpx

from app.models import Response
from app.utils.log import log_event


type ImageReadSource = Literal["direct_path", "direct_url", "napcat_refresh"]


class NapCatImageBot(Protocol):
    """描述刷新 NapCat 图片信息所需的最小 BOT 能力。"""

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """获取图片文件信息。"""
        ...


@dataclass(frozen=True)
class NapCatImageResource:
    """描述一张可从消息段或 NapCat 接口读取的图片。"""

    label: str
    file: str | None = None
    file_id: str | None = None
    path: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class NapCatImageReadResult:
    """描述单张图片的读取结果。"""

    resource: NapCatImageResource
    image_bytes: bytes | None
    source: ImageReadSource | None
    error_type: str | None
    error: str | None

    @property
    def ok(self) -> bool:
        """图片是否读取成功。"""
        return self.image_bytes is not None


class NapCatImageReader:
    """按本地路径、现有 URL、NapCat 刷新的顺序读取图片。"""

    def __init__(
        self,
        *,
        bot: NapCatImageBot,
        http_client: httpx.AsyncClient | None,
        fetch_concurrency: int,
        download_timeout_seconds: float,
    ) -> None:
        """保存读取图片所需依赖和并发边界。"""
        if fetch_concurrency < 1:
            raise ValueError("图片读取并发数必须大于等于 1")
        if download_timeout_seconds <= 0:
            raise ValueError("图片下载超时必须大于 0")
        self.bot: NapCatImageBot = bot
        self.http_client: httpx.AsyncClient | None = http_client
        self.fetch_concurrency: int = fetch_concurrency
        self.download_timeout_seconds: float = download_timeout_seconds

    async def read_many(
        self, *, resources: list[NapCatImageResource]
    ) -> list[NapCatImageReadResult]:
        """并发读取图片并保持输入顺序。"""
        semaphore = asyncio.Semaphore(self.fetch_concurrency)

        async def read_one(resource: NapCatImageResource) -> NapCatImageReadResult:
            async with semaphore:
                return await self.read(resource=resource)

        return list(await asyncio.gather(*(read_one(item) for item in resources)))

    async def read(self, *, resource: NapCatImageResource) -> NapCatImageReadResult:
        """读取单张图片，直接来源失败后再尝试 NapCat 刷新。"""
        failures: list[str] = []
        failure_types: list[str] = []
        error_type = "ImageContentUnavailable"
        direct_result = await self._read_direct(
            resource=resource,
            failures=failures,
            failure_types=failure_types,
        )
        if direct_result is not None:
            return self._finish_success(
                resource=resource,
                image_bytes=direct_result[0],
                source=direct_result[1],
            )
        if failure_types:
            error_type = failure_types[-1]

        if self._has_text(resource.file) or self._has_text(resource.file_id):
            try:
                response = await self._refresh_image_info(resource=resource)
            except Exception as exc:
                error_type = type(exc).__name__
                failures.append(f"NapCat 刷新图片信息失败: {exc}")
            else:
                if response.status != "ok" or response.retcode != 0:
                    error_type = "NapCatActionFailed"
                    detail = response.message or response.wording or "NapCat 返回失败"
                    failures.append(f"NapCat 刷新图片信息失败: {detail}")
                else:
                    try:
                        image_bytes = await self._read_refreshed_response(
                            response=response,
                            failures=failures,
                        )
                    except Exception as exc:
                        error_type = type(exc).__name__
                        failures.append(f"读取 NapCat 刷新结果失败: {exc}")
                        image_bytes = None
                    if image_bytes is not None:
                        return self._finish_success(
                            resource=resource,
                            image_bytes=image_bytes,
                            source="napcat_refresh",
                        )
        else:
            if not failure_types:
                error_type = "MissingImageIdentifier"
            failures.append("图片段缺少可用于 NapCat 刷新的 file 或 file_id")

        error = "；".join(failures) if failures else "图片没有可读取内容"
        return self._finish_error(
            resource=resource,
            error_type=error_type,
            error=error,
        )

    async def _read_direct(
        self,
        *,
        resource: NapCatImageResource,
        failures: list[str],
        failure_types: list[str],
    ) -> tuple[bytes, ImageReadSource] | None:
        """优先读取消息段已有的本地路径和 URL。"""
        if self._has_text(resource.path):
            path = Path(resource.path or "")
            if path.is_file():
                try:
                    async with aiofiles.open(path, mode="rb") as file:
                        return await file.read(), "direct_path"
                except Exception as exc:
                    failure_types.append(type(exc).__name__)
                    failures.append(f"读取本地路径失败: {exc}")
            else:
                failure_types.append("FileNotFoundError")
                failures.append(f"本地路径不存在: {path}")
        if self._has_text(resource.url):
            try:
                return await self._download_url(url=resource.url or ""), "direct_url"
            except Exception as exc:
                failure_types.append(type(exc).__name__)
                failures.append(f"下载现有 URL 失败: {exc}")
        return None

    async def _refresh_image_info(self, *, resource: NapCatImageResource) -> Response:
        """通过 NapCat 获取新的图片来源信息。"""
        if self._has_text(resource.file):
            return await self.bot.get_image(file=resource.file)
        return await self.bot.get_image(file_id=resource.file_id)

    async def _read_refreshed_response(
        self, *, response: Response, failures: list[str]
    ) -> bytes | None:
        """读取 NapCat 响应中的 base64、本地路径或 URL。"""
        data = response.data if isinstance(response.data, dict) else {}
        raw_base64 = data.get("base64")
        if isinstance(raw_base64, str) and raw_base64.strip() != "":
            try:
                return base64.b64decode(raw_base64, validate=True)
            except binascii.Error as exc:
                failures.append(f"NapCat 返回的 base64 无效: {exc}")
        for key in ("path", "file"):
            value = data.get(key)
            if not isinstance(value, str) or value.strip() == "":
                continue
            path = Path(value)
            if not path.is_file():
                failures.append(f"NapCat 返回的本地路径不存在: {path}")
                continue
            try:
                async with aiofiles.open(path, mode="rb") as file:
                    return await file.read()
            except Exception as exc:
                failures.append(f"读取 NapCat 本地路径失败: {exc}")
        url = data.get("url")
        if isinstance(url, str) and url.strip() != "":
            try:
                return await self._download_url(url=url)
            except Exception as exc:
                failures.append(f"下载 NapCat 刷新 URL 失败: {exc}")
        return None

    async def _download_url(self, *, url: str) -> bytes:
        """通过 MyBot 本地 HTTP 客户端下载图片。"""
        if self.http_client is None:
            raise RuntimeError("图片 URL 下载需要配置 HTTP 客户端")
        response = await self.http_client.get(
            url,
            timeout=self.download_timeout_seconds,
        )
        response.raise_for_status()
        return response.content

    def _finish_success(
        self,
        *,
        resource: NapCatImageResource,
        image_bytes: bytes,
        source: ImageReadSource,
    ) -> NapCatImageReadResult:
        """记录并返回成功结果。"""
        result = NapCatImageReadResult(
            resource=resource,
            image_bytes=image_bytes,
            source=source,
            error_type=None,
            error=None,
        )
        self._log_result(result=result)
        return result

    def _finish_error(
        self,
        *,
        resource: NapCatImageResource,
        error_type: str,
        error: str,
    ) -> NapCatImageReadResult:
        """记录并返回失败结果。"""
        result = NapCatImageReadResult(
            resource=resource,
            image_bytes=None,
            source=None,
            error_type=error_type,
            error=error,
        )
        self._log_result(result=result)
        return result

    def _log_result(self, *, result: NapCatImageReadResult) -> None:
        """记录资源字段、最终来源和失败原因。"""
        resource = result.resource
        log_event(
            level="DEBUG" if result.ok else "WARNING",
            event="napcat.image_reader.finished",
            category="napcat_tools",
            message="NapCat 图片资源读取完成",
            resource_type="image",
            label=resource.label,
            path=resource.path,
            has_url=self._has_text(resource.url),
            file=resource.file,
            file_id=resource.file_id,
            source=result.source,
            ok=result.ok,
            bytes_count=len(result.image_bytes or b""),
            error_type=result.error_type,
            error=result.error,
        )

    def _has_text(self, value: str | None) -> bool:
        """判断可选字符串是否包含有效内容。"""
        return value is not None and value.strip() != ""
