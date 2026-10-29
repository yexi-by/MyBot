"""OpenAI 标准图像生成与编辑工具；独立连接配置，与会话服务商解耦。"""

import asyncio
import base64
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import Field, model_validator

from .contracts import (
    JSON_OBJECT,
    GeneratedImage,
    JsonObject,
    JsonValue,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
)
from .diagnostics import remote_error_metadata
from .napcat.arguments import ReadMedia
from .napcat.media import MediaReader
from .napcat.tools import media_segments
from .providers import permanent_request_error, retry_after_seconds, retry_delay
from .settings import ImageSettings, NapCatSettings

CATEGORY_LABELS = {"media.generate": "生成图片"}
MAX_GENERATED_IMAGES = 16


def detect_image_format(content: bytes) -> tuple[str, str]:
    """按图片文件头识别 MIME 类型与扩展名，拒绝非图片数据。"""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise ValueError("仅支持 PNG、JPEG、GIF 或 WebP 格式的有效图片")


class ReferenceImage(Model):
    message_id: str | None = Field(
        default=None, min_length=1, description="当前会话消息 ID；引用消息或合并转发中的图片"
    )
    index: int = Field(
        default=0, ge=0, description="消息内媒体的零起点索引，仅配合 message_id 使用"
    )
    forward_path: list[str] = Field(
        default_factory=list, description="消息内嵌套转发路径，仅配合 message_id 使用"
    )
    include_forwards: bool = Field(
        default=True, description="是否展开消息内合并转发中的媒体，仅配合 message_id 使用"
    )
    user_id: str | None = Field(
        default=None, pattern=r"^[1-9][0-9]*$", description="引用指定 QQ 号的公开头像"
    )
    group_avatar: bool = Field(default=False, description="引用当前目标群的群头像")
    media_url: str | None = Field(
        default=None,
        min_length=1,
        description="引用 QQ 官方图床 URL（qpic.cn、qlogo.cn、nt.qq.com.cn）",
    )
    workspace_path: str | None = Field(
        default=None, min_length=1, description="引用共享沙箱工作区内的图片相对路径"
    )
    generated_id: str | None = Field(
        default=None, min_length=1, description="引用当前会话已生成的图片 ID"
    )

    @model_validator(mode="after")
    def validate_source(self) -> ReferenceImage:
        sources = sum(
            (
                bool(self.message_id),
                bool(self.user_id),
                self.group_avatar,
                bool(self.media_url),
                bool(self.workspace_path),
                bool(self.generated_id),
            )
        )
        if sources != 1:
            raise ValueError(
                "每张参考图必须且只能指定 message_id、user_id、group_avatar、"
                "media_url、workspace_path 或 generated_id 一种来源"
            )
        if (self.index != 0 or self.forward_path) and self.message_id is None:
            raise ValueError("index 与 forward_path 只能配合 message_id 使用")
        return self


class GenerateImageInput(Model):
    prompt: str = Field(min_length=1, description="生图画面描述或图生图修改指令")
    images: list[ReferenceImage] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "参考图列表；为空时调用 /images/generations 文生图，"
            "非空时调用 /images/edits 图生图或改图"
        ),
    )
    n: int = Field(default=1, ge=1, le=4, description="生成图片张数")
    size: str | None = Field(
        default=None,
        min_length=1,
        description="图片尺寸或比例，如 1024x1024、1536x1024、1024x1536、auto；省略时由接口默认",
    )
    quality: str | None = Field(
        default=None,
        min_length=1,
        description="画面质量，如 auto、low、medium、high、standard、hd；省略时由接口默认",
    )
    background: str | None = Field(
        default=None,
        min_length=1,
        description="背景设置，如 auto、transparent、opaque；省略时由接口默认",
    )
    output_format: Literal["png", "jpeg", "webp"] | None = Field(
        default=None, description="输出图片格式；省略时由接口默认"
    )
    style: str | None = Field(
        default=None,
        min_length=1,
        description="画面风格，如 vivid、natural；省略时由接口默认",
    )
    response_format: Literal["b64_json", "url"] | None = Field(
        default=None,
        description="返回格式；省略时不发送该字段，兼容默认返回 b64_json 的模型",
    )
    workspace_path: str | None = Field(
        default=None,
        min_length=1,
        description="可选：将生成的图片同时保存到共享沙箱工作区的相对路径",
    )
    inspect: bool = Field(
        default=False,
        description="是否将生成的图片加入当前模型的多模态上下文供查看；默认 false 以节省上下文",
    )


class _ImageRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def _workspace_destinations(path: str, count: int, extensions: list[str]) -> list[str]:
    if count == 1:
        return [path]
    pure = PurePosixPath(path.replace("\\", "/"))
    stem = pure.stem or "image"
    parent = str(pure.parent)
    prefix = "" if parent in ("", ".") else parent.rstrip("/") + "/"
    result: list[str] = []
    for idx in range(count):
        suffix = pure.suffix or f".{extensions[idx]}"
        result.append(f"{prefix}{stem}_{idx + 1}{suffix}")
    return result


async def _load_reference(
    context: ToolContext, ref: ReferenceImage, reader: MediaReader
) -> tuple[bytes, str, str]:
    if ref.generated_id is not None:
        generated = context.generated_images.get(ref.generated_id)
        if generated is None:
            raise ValueError(f"参考图 generated_id ({ref.generated_id}) 不存在或已过期")
        mime, ext = detect_image_format(generated.content)
        return generated.content, mime, ext
    if ref.workspace_path is not None:
        denied = context.check_tool(
            "sandbox__read_file", {"path": ref.workspace_path, "offset": 0, "limit": 65_536}
        )
        if denied is not None:
            raise PermissionError(denied.message or "当前会话未开放共享工作区读取权限")
        if context.read_workspace is None:
            raise ValueError("共享工作区读取服务不可用")
        content = await context.read_workspace(ref.workspace_path)
        if len(content) > reader.settings.media_max_bytes:
            raise ValueError("参考图超过媒体体积上限")
        mime, ext = detect_image_format(content)
        return content, mime, ext
    read_req = ReadMedia(
        message_id=ref.message_id,
        user_id=ref.user_id,
        group_avatar=ref.group_avatar,
        media_url=ref.media_url,
        forward_path=ref.forward_path,
        include_forwards=ref.include_forwards,
        indexes=[ref.index] if ref.message_id is not None else None,
    )
    denied = context.check_tool("qq__read_media", read_req.model_dump(mode="json"))
    if denied is not None:
        raise PermissionError(denied.message or "当前会话未开放读取参考图所需的媒体权限")
    selection = await media_segments(context, read_req)
    if len(selection.segments) != 1:
        raise ValueError("未找到指定的参考图媒体段")
    asset = await reader.read(selection.segments[0], context.napcat)
    mime, ext = detect_image_format(asset.content)
    return asset.content, mime, ext


async def _download_generated_url(
    source: str,
    config: ImageSettings,
    reader: MediaReader,
    client: httpx.AsyncClient,
) -> bytes:
    if not config.proxy:
        content, _ = await reader.download(source)
        return content
    try:
        url = httpx.URL(source)
    except httpx.InvalidURL as exc:
        raise ValueError("生图接口返回的图片 URL 格式无效") from exc
    for _ in range(10):
        if url.scheme not in ("http", "https") or not url.host or url.username or url.password:
            raise ValueError("生图结果 URL 必须使用无凭据的 HTTP/HTTPS 地址")
        await reader.resolver(url.host)
        async with asyncio.timeout(config.timeout):
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("生图结果 URL 重定向缺少目标")
                    url = url.join(location)
                    continue
                if response.status_code >= 400:
                    raise ValueError(f"生图结果 URL 下载失败（HTTP {response.status_code}）")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > reader.settings.media_max_bytes:
                        raise ValueError("生成的图片超过媒体大小上限")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError("生图结果 URL 重定向次数超过限制")


async def _post_image_request(
    client: httpx.AsyncClient,
    config: ImageSettings,
    request: GenerateImageInput,
    references: list[tuple[bytes, str, str]],
) -> JsonObject:
    base = config.endpoint_base()
    optional_fields = (
        ("size", request.size),
        ("quality", request.quality),
        ("background", request.background),
        ("output_format", request.output_format),
        ("style", request.style),
        ("response_format", request.response_format),
    )
    try:
        async with asyncio.timeout(config.timeout):
            if not references:
                body: JsonObject = {
                    "model": config.model,
                    "prompt": request.prompt,
                    "n": request.n,
                }
                for key, value in optional_fields:
                    if value is not None:
                        body[key] = value
                response = await client.post(
                    f"{base}/images/generations",
                    headers=config.request_headers(),
                    json=body,
                    timeout=config.timeout,
                )
            else:
                form_data: dict[str, str] = {
                    "model": config.model,
                    "prompt": request.prompt,
                    "n": str(request.n),
                }
                for key, value in optional_fields:
                    if value is not None:
                        form_data[key] = str(value)
                headers = {
                    k: v for k, v in config.request_headers().items() if k.lower() != "content-type"
                }
                files = [
                    ("image", (f"image_{index}.{ext}", content, mime))
                    for index, (content, mime, ext) in enumerate(references)
                ]
                response = await client.post(
                    f"{base}/images/edits",
                    headers=headers,
                    data=form_data,
                    files=files,
                    timeout=config.timeout,
                )
        if response.is_error:
            error_obj: JsonObject = {}
            if len(response.content) <= 65_536:
                try:
                    error_obj = JSON_OBJECT.validate_json(response.content)
                except ValueError:
                    error_obj = {}
            details = remote_error_metadata(error_obj)
            raise _ImageRequestError(
                f"生图接口返回 HTTP {response.status_code}" + (f"；{details}" if details else ""),
                retryable=(response.status_code in (408, 409, 429) or response.status_code >= 500)
                and not permanent_request_error(error_obj),
                retry_after=retry_after_seconds(response.headers.get("retry-after")),
            )
        payload = JSON_OBJECT.validate_json(response.content)
        if payload.get("error"):
            details = remote_error_metadata(payload)
            raise _ImageRequestError(
                "生图接口返回错误" + (f"；{details}" if details else ""),
                retryable=not permanent_request_error(payload),
                retry_after=retry_after_seconds(response.headers.get("retry-after")),
            )
        return payload
    except TimeoutError:
        raise _ImageRequestError("生图接口请求超时", retryable=True) from None
    except httpx.HTTPError as transport_error:
        reason = type(transport_error).__name__
        raise _ImageRequestError(
            f"生图接口连接失败（{reason}）",
            retryable=isinstance(
                transport_error,
                (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.RemoteProtocolError,
                    httpx.ProxyError,
                ),
            ),
        ) from None
    except ValueError:
        raise _ImageRequestError("生图接口响应不是有效 JSON 对象", retryable=True) from None


def build_tools(
    get_image_settings: Callable[[], ImageSettings],
    napcat_settings: NapCatSettings | None = None,
    *,
    reader: MediaReader | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[ToolDefinition]:
    media_reader = reader or MediaReader(napcat_settings)

    async def generate(context: ToolContext, request: GenerateImageInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        config = get_image_settings()
        if not config.enabled or not config.base_url.strip() or not config.model.strip():
            return ToolResult.failure("生图服务尚未启用或未完成配置", "PermissionDenied")
        if request.workspace_path is not None:
            denied = context.check_tool(
                "sandbox__write_file",
                {
                    "path": request.workspace_path,
                    "content": "",
                    "overwrite": False,
                    "append": False,
                },
            )
            if denied is not None:
                return denied
            if context.write_workspace is None:
                return ToolResult.failure("沙箱文件写入服务不可用", "SandboxError")

        references: list[tuple[bytes, str, str]] = []
        try:
            for ref in request.images:
                if context.cancelled():
                    return ToolResult(status="cancelled", message="当前轮次已停止")
                if denied := context.recheck():
                    return denied
                references.append(await _load_reference(context, ref, media_reader))
        except PermissionError as exc:
            return ToolResult.failure(str(exc), "PermissionDenied")

        owned_client = client is None
        http_client = client or httpx.AsyncClient(
            timeout=config.timeout, proxy=config.proxy, trust_env=False
        )
        try:
            payload: JsonObject | None = None
            for retry in range(config.max_retries + 1):
                if context.cancelled():
                    return ToolResult(status="cancelled", message="当前轮次已停止")
                if denied := context.recheck():
                    return denied
                try:
                    payload = await _post_image_request(http_client, config, request, references)
                    break
                except _ImageRequestError as error:
                    if not error.retryable or retry == config.max_retries:
                        suffix = f"；已重试 {retry} 次" if retry else ""
                        return ToolResult.failure(f"{error}{suffix}", "ImageGenerationError")
                    await asyncio.sleep(retry_delay(retry + 1, error.retry_after))
            assert payload is not None
            raw_items = payload.get("data")
            if not isinstance(raw_items, list) or not raw_items:
                return ToolResult.failure("生图接口未返回有效图片数据", "ImageGenerationError")

            decoded: list[tuple[bytes, str, str, str | None]] = []
            total_bytes = 0
            max_bytes = media_reader.settings.media_max_bytes
            for entry in raw_items[: request.n]:
                if not isinstance(entry, dict):
                    continue
                b64 = entry.get("b64_json") or entry.get("b64")
                url = entry.get("url")
                if isinstance(b64, str) and b64:
                    if len(b64) > max_bytes * 4 // 3 + 16:
                        return ToolResult.failure(
                            "生成的图片超过媒体大小上限", "ImageGenerationError"
                        )
                    content = base64.b64decode(b64, validate=True)
                elif isinstance(url, str) and url:
                    content = await _download_generated_url(url, config, media_reader, http_client)
                else:
                    continue
                if len(content) > max_bytes or total_bytes + len(content) > max_bytes:
                    return ToolResult.failure("生成的图片超过媒体大小上限", "ImageGenerationError")
                total_bytes += len(content)
                mime, ext = detect_image_format(content)
                revised = entry.get("revised_prompt")
                decoded.append(
                    (content, mime, ext, revised if isinstance(revised, str) and revised else None)
                )

            if not decoded:
                return ToolResult.failure("生图接口未返回可解析的图片内容", "ImageGenerationError")

            paths: list[str] = []
            if request.workspace_path is not None:
                assert context.write_workspace is not None
                destinations = _workspace_destinations(
                    request.workspace_path,
                    len(decoded),
                    [ext for _, _, ext, _ in decoded],
                )
                for dest, (content, _, _, _) in zip(destinations, decoded, strict=True):
                    if denied := context.recheck():
                        return denied
                    await context.write_workspace(dest, content)
                    paths.append(dest)

            images_out: list[JsonValue] = []
            media_out: list[JsonValue] = []
            for idx, (content, mime, _ext, revised) in enumerate(decoded):
                gen_id = f"gen_{uuid4().hex[:12]}"
                context.generated_images[gen_id] = GeneratedImage(gen_id, content, mime)
                context.generated_images.move_to_end(gen_id)
                while len(context.generated_images) > MAX_GENERATED_IMAGES:
                    context.generated_images.popitem(last=False)
                info: JsonObject = {
                    "generated_id": gen_id,
                    "mime_type": mime,
                    "size": len(content),
                }
                if revised is not None:
                    info["revised_prompt"] = revised
                if idx < len(paths):
                    info["workspace_path"] = paths[idx]
                images_out.append(info)
                if request.inspect:
                    media_out.append(
                        {
                            "type": "image",
                            "mime_type": mime,
                            "base64": base64.b64encode(content).decode("ascii"),
                        }
                    )

            result_data: JsonObject = {
                "mode": "edits" if references else "generations",
                "images": images_out,
            }
            if media_out:
                result_data["media"] = media_out
            return ToolResult.success(result_data)
        finally:
            if owned_client:
                await http_client.aclose()

    return [
        ToolDefinition(
            "image__generate",
            (
                "生成或编辑图片（OpenAI 标准格式）。不传 images 时调用 /images/generations 文生图；"
                "传入 images 时调用 /images/edits 图生图或改图（支持直接引用当前会话消息图片、QQ 头像、"
                "群头像、QQ 官方图床、共享工作区图片或此前生成的 generated_id）。"
                "生成的图片返回 generated_id，可在 qq__send_message 的 image/file 段或头像工具中直接通过 "
                "generated_id 发送，无需依赖共享沙箱。"
            ),
            "media.generate",
            model_binding(GenerateImageInput, generate),
            title="生成或编辑图片",
        )
    ]
