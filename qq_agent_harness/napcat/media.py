"""仅本轮使用的多媒体读取；本地受限路径、HTTP、NapCat 刷新依次尝试。"""

import asyncio
import base64
import ipaddress
import logging
import mimetypes
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from ..contracts import JsonObject, NapCatConnection
from ..settings import NapCatSettings
from .connection import NapCatError, object_value
from .media_sources import qq_media_url

logger = logging.getLogger(__name__)


async def resolve_public(host: str) -> str:
    try:
        records = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("媒体服务器域名无法解析") from exc
    addresses = list(dict.fromkeys(str(record[4][0]) for record in records))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("媒体 URL 不允许访问本机、内网或保留地址")
    return addresses[0]


@dataclass(frozen=True)
class MediaAsset:
    type: Literal["image", "record", "video", "file"]
    content: bytes
    mime_type: str
    source: str

    def public(self) -> JsonObject:
        return {
            "type": "audio" if self.type == "record" else self.type,
            "mime_type": self.mime_type,
            "base64": base64.b64encode(self.content).decode("ascii"),
            "size": len(self.content),
            "source": self.source,
        }


@dataclass(frozen=True)
class MediaFailure:
    index: int
    error_type: str
    message: str

    def public(self) -> JsonObject:
        return {"index": self.index, "error_type": self.error_type, "message": self.message}


@dataclass
class MediaBatch:
    media: list[tuple[int, MediaAsset]] = field(default_factory=list)
    errors: list[MediaFailure] = field(default_factory=list)


class MediaReader:
    def __init__(
        self,
        settings: NapCatSettings | None = None,
        *,
        local_roots: Sequence[Path] = (),
        client: httpx.AsyncClient | None = None,
        resolver: Callable[[str], Awaitable[str]] = resolve_public,
    ):
        self.settings = settings or NapCatSettings()
        self.local_roots = tuple(path.resolve() for path in local_roots)
        self.client = client
        self.resolver = resolver
        self.semaphore = asyncio.Semaphore(self.settings.media_concurrency)

    async def download(self, source: str) -> tuple[bytes, str]:
        return await self._download(source)

    async def _download(self, source: str, *, qq_image: bool = False) -> tuple[bytes, str]:
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            trust_env=False, timeout=self.settings.media_timeout
        )
        try:
            try:
                url = httpx.URL(source)
            except httpx.InvalidURL as exc:
                raise ValueError("媒体 URL 格式无效") from exc
            for _ in range(20):
                if qq_image:
                    url = qq_media_url(str(url))
                if (
                    url.scheme not in ("http", "https")
                    or not url.host
                    or url.username
                    or url.password
                ):
                    raise ValueError("媒体必须使用无凭据的 HTTP/HTTPS URL")
                ip = await self.resolver(url.host)
                pinned = url.copy_with(host=ip)
                headers = {"Host": url.netloc.decode("ascii")}
                # 固定已验证的目标 IP，TLS 仍验证原域名，避免校验后再次 DNS 解析。
                async with client.stream(
                    "GET", pinned, headers=headers, extensions={"sni_hostname": url.host}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("媒体重定向缺少目标")
                        try:
                            url = url.join(location)
                        except httpx.InvalidURL as exc:
                            raise ValueError("媒体重定向 URL 格式无效") from exc
                        continue
                    if response.status_code >= 400:
                        raise ValueError(f"媒体 HTTP 下载失败（{response.status_code}）")
                    length = response.headers.get("content-length")
                    if length:
                        if not length.isascii() or not length.isdecimal():
                            raise ValueError("媒体 HTTP 长度格式无效")
                        if int(length) > self.settings.media_max_bytes:
                            raise ValueError("媒体超过配置的体积上限")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.settings.media_max_bytes:
                            raise ValueError("媒体超过配置的体积上限")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    mime = response.headers.get("content-type", "application/octet-stream").split(
                        ";"
                    )[0]
                    if qq_image:
                        # QQ 图床可能返回 octet-stream；按图片头识别，拒绝 HTML 错误页与任意文件。
                        if content.startswith(b"\x89PNG\r\n\x1a\n"):
                            mime = "image/png"
                        elif content.startswith(b"\xff\xd8\xff"):
                            mime = "image/jpeg"
                        elif content.startswith((b"GIF87a", b"GIF89a")):
                            mime = "image/gif"
                        elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
                            mime = "image/webp"
                        else:
                            raise ValueError("QQ 图床未返回支持的 PNG、JPEG、GIF 或 WebP 图片")
                    return content, mime
            raise ValueError("媒体重定向次数超过限制")
        finally:
            if own_client:
                await client.aclose()

    async def _local(self, name: str) -> bytes | None:
        if (
            not self.local_roots
            or not name
            or name.startswith(("http://", "https://", "base64://"))
        ):
            return None
        candidate = Path(name).resolve()
        if not any(candidate.is_relative_to(root) for root in self.local_roots):
            return None

        def read() -> bytes | None:
            if not candidate.is_file():
                return None
            with candidate.open("rb") as stream:
                data = stream.read(self.settings.media_max_bytes + 1)
            if len(data) > self.settings.media_max_bytes:
                raise ValueError("本地媒体超过体积上限")
            return data

        return await asyncio.to_thread(read)

    async def read(self, segment: JsonObject, connection: NapCatConnection) -> MediaAsset:
        async with self.semaphore:
            try:
                return await self._read(segment, connection)
            except TimeoutError:
                data = segment.get("data")
                data = data if isinstance(data, dict) else {}
                logger.warning(
                    "napcat_media_read_failed",
                    extra={
                        "resource_type": str(segment.get("type", "file")),
                        "source_path": "<resource>",
                        "has_url": bool(data.get("url")),
                        "final_source": None,
                        "failure_reason": "TimeoutError",
                    },
                )
                raise

    async def _read(self, segment: JsonObject, connection: NapCatConnection) -> MediaAsset:
        kind = segment.get("type")
        if kind not in ("image", "record", "video", "file"):
            raise ValueError("不支持的媒体消息段类型")
        data = segment.get("data")
        if not isinstance(data, dict):
            raise ValueError("媒体消息段缺少 data")
        if data.get("qq_image") is True:
            url = data.get("url")
            if not isinstance(url, str):
                raise ValueError("QQ 图片缺少 URL")
            try:
                content, mime = await self._download(url, qq_image=True)
            except httpx.HTTPError as exc:
                # HTTP 异常可携带带签名的图床 URL，不回传原始异常正文。
                raise ValueError("QQ 图片下载连接失败或超时") from exc
            return MediaAsset("image", content, mime, "qq_http")
        original_path = str(data.get("path") or data.get("file") or "")
        has_url = isinstance(data.get("url"), str) and bool(data["url"])
        details = {
            "resource_type": kind,
            "source_path": "<url>" if "://" in original_path else original_path,
            "has_url": has_url,
        }
        failures: list[str] = []

        async def from_fields(fields: JsonObject, prefix: str) -> MediaAsset | None:
            name = str(fields.get("path") or fields.get("file") or "")
            local = await self._local(name)
            if local is not None:
                return MediaAsset(
                    kind,
                    local,
                    mimetypes.guess_type(name)[0] or "application/octet-stream",
                    prefix + "local",
                )
            url = fields.get("url") or (name if name.startswith(("https://", "http://")) else None)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                try:
                    content, mime = await self.download(url)
                    return MediaAsset(kind, content, mime, prefix + "http")
                except (ValueError, httpx.HTTPError, OSError) as exc:
                    failures.append(type(exc).__name__)
            encoded = fields.get("base64") or (
                name.removeprefix("base64://") if name.startswith("base64://") else None
            )
            if isinstance(encoded, str) and encoded:
                if len(encoded) > self.settings.media_max_bytes * 4 // 3 + 16:
                    raise ValueError("NapCat 媒体超过体积上限")
                content = base64.b64decode(encoded, validate=True)
                if len(content) > self.settings.media_max_bytes:
                    raise ValueError("NapCat 媒体超过体积上限")
                return MediaAsset(
                    kind,
                    content,
                    mimetypes.guess_type(str(fields.get("file_name", name)))[0]
                    or "application/octet-stream",
                    prefix + "base64",
                )
            return None

        try:
            asset = await from_fields(data, "")
            if asset:
                logger.info(
                    "napcat_media_read",
                    extra={**details, "final_source": asset.source, "failure_reason": None},
                )
                return asset
            file_id = data.get("file_id") or data.get("file")
            if (
                not isinstance(file_id, str)
                or not file_id
                or file_id.startswith(("file:", "/", "\\"))
            ):
                raise ValueError("媒体缺少可刷新的 NapCat 文件标识")
            action = (
                "get_image" if kind == "image" else "get_record" if kind == "record" else "get_file"
            )
            params: JsonObject = {"file": file_id}
            if action == "get_record":
                params["out_format"] = "mp3"
            refreshed = object_value(
                await connection.call(action, params, timeout=self.settings.media_timeout)
            )
            if action == "get_record":
                # 锁定版本此接口带 out_format 会返回转换后 base64，但 file_name 仍可能是原始 silk。
                refreshed["file_name"] = "audio.mp3"
            asset = await from_fields(refreshed, "napcat_")
            if asset is None:
                # 原生资源标识来自已验证消息，不接受模型提供的任意远端路径。
                stream_action = (
                    "download_file_record_stream" if kind == "record" else "download_file_stream"
                )
                stream_params: JsonObject = {"file_id": file_id, "chunk_size": 65_536}
                if kind == "record":
                    stream_params["out_format"] = "mp3"
                streamed = object_value(
                    await connection.call(
                        stream_action, stream_params, timeout=self.settings.media_timeout
                    )
                )
                if kind == "record":
                    streamed["file_name"] = "audio.mp3"
                asset = await from_fields(streamed, "napcat_stream_")
            if asset is None:
                raise ValueError("NapCat 下载流未返回可读内容；不会把远端路径当作本地路径读取")
            logger.info(
                "napcat_media_read",
                extra={
                    **details,
                    "final_source": asset.source,
                    "failure_reason": ",".join(failures) or None,
                },
            )
            return asset
        except (ValueError, OSError, httpx.HTTPError, TimeoutError) as exc:
            logger.warning(
                "napcat_media_read_failed",
                extra={**details, "final_source": None, "failure_reason": type(exc).__name__},
            )
            raise

    async def read_many(
        self, segments: Sequence[JsonObject], connection: NapCatConnection
    ) -> MediaBatch:
        selected = [
            (index, segment)
            for index, segment in enumerate(segments)
            if segment.get("type") in ("image", "record", "video", "file")
        ]
        total_bytes = 0

        async def one(index: int, segment: JsonObject) -> tuple[int, MediaAsset | Exception]:
            nonlocal total_bytes
            try:
                asset = await self.read(segment, connection)
                if total_bytes + len(asset.content) > self.settings.media_max_bytes:
                    raise ValueError("本批媒体总量超过配置上限")
                total_bytes += len(asset.content)
                return index, asset
            except (NapCatError, ValueError, OSError, httpx.HTTPError, TimeoutError) as exc:
                return index, exc

        # gather 的并发由 read() 内配置的 semaphore 限制，单项失败不阻断整批。
        results = await asyncio.gather(*(one(index, segment) for index, segment in selected))
        batch = MediaBatch()
        for index, result in results:
            if isinstance(result, Exception):
                batch.errors.append(MediaFailure(index, type(result).__name__, str(result)))
            else:
                batch.media.append((index, result))
        return batch
