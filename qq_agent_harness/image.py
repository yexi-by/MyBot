"""多协议图像生成与编辑工具；独立连接配置，与会话服务商解耦。"""

import asyncio
import base64
import binascii
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import Field, model_validator

from .contracts import (
    JSON_OBJECT,
    GeneratedMedia,
    JsonObject,
    JsonValue,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
    remember_generated,
)
from .diagnostics import remote_error_metadata
from .napcat.arguments import ReadMedia
from .napcat.media import MediaReader
from .napcat.tools import media_segments
from .providers import permanent_request_error, retry_after_seconds, retry_delay
from .settings import ImageProtocol, ImageSettings, NapCatSettings

CATEGORY_LABELS = {"image": "生成图片"}

_DATA_IMAGE_URL = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp)(?:;[^,]*)?;base64,(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_MARKDOWN_IMAGE = re.compile(
    r"!\[[^\]]*\]\(\s*(data:image/(?:png|jpe?g|gif|webp)(?:;[^,)]*)?;base64,[A-Za-z0-9+/=\s]+|https?://[^\s)]+)\s*\)",
    re.IGNORECASE,
)
_INLINE_DATA_IMAGE = re.compile(
    r"data:image/(?:png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)
_DIMENSIONS = re.compile(r"^(\d{2,5})\s*[xX*×]\s*(\d{2,5})$")
_ASPECT_RATIO = re.compile(r"^(\d{1,2})\s*:\s*(\d{1,2})$")
_RESOLUTION_TIER = re.compile(r"^(512|1k|2k|4k)$", re.IGNORECASE)
_GEMINI_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")

_KNOWN_RATIOS: tuple[tuple[int, int], ...] = (
    (1, 1),
    (16, 9),
    (9, 16),
    (4, 3),
    (3, 4),
    (3, 2),
    (2, 3),
    (21, 9),
    (9, 21),
    (5, 4),
    (4, 5),
    (20, 9),
    (9, 20),
)

_SINGLE_OUTPUT_PROTOCOLS = frozenset({"chat_completions", "responses", "gemini", "ark_images"})

_PERMANENT_STATUS_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "FAILED_PRECONDITION",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "InvalidApiKey",
        "InvalidParameter",
        "AccessDenied",
        "ModelNotFound",
        "DataInspectionFailed",
        "BadRequest.EmptyInput",
        "BadRequest.TooLarge",
    }
)
_KNOWN_ERROR_CODES = _PERMANENT_STATUS_CODES | frozenset(
    {
        "RESOURCE_EXHAUSTED",
        "INTERNAL",
        "UNAVAILABLE",
        "DEADLINE_EXCEEDED",
        "Throttling",
        "Throttling.RateQuota",
        "Throttling.AllocationQuota",
        "InternalError",
        "ServiceUnavailable",
    }
)
_GEMINI_BLOCK_REASONS = frozenset(
    {
        "SAFETY",
        "IMAGE_SAFETY",
        "PROHIBITED_CONTENT",
        "RECITATION",
        "BLOCKLIST",
        "SPII",
        "OTHER",
        "NO_IMAGE",
    }
)


@dataclass(frozen=True)
class _ExtractedImage:
    b64: str | None = None
    url: str | None = None
    revised_prompt: str | None = None


@dataclass(frozen=True)
class ImagePreviewResult:
    content: bytes
    mime_type: str
    protocol: ImageProtocol
    endpoint: str
    elapsed_ms: int
    revised_prompt: str | None = None


def protocol_endpoint_label(protocol: ImageProtocol, *, has_references: bool = False) -> str:
    """返回不含密钥与动态输入的协议端点模板，用于错误诊断与控制台提示。"""
    if protocol in ("openai_images", "xai_images"):
        return "/images/edits" if has_references else "/images/generations"
    if protocol == "chat_completions":
        return "/chat/completions"
    if protocol == "responses":
        return "/responses"
    if protocol == "gemini":
        return "/models/{model}:generateContent"
    if protocol == "ark_images":
        return "/images/generations"
    return "/services/aigc/multimodal-generation/generation"


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


def _encode_data_url(content: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def _decode_base64_image(raw_b64: str, max_bytes: int) -> bytes:
    compact = re.sub(r"\s+", "", raw_b64)
    if not compact:
        raise ValueError("生图接口返回的图片数据为空")
    if len(compact) > max_bytes * 4 // 3 + 16:
        raise ValueError("生成的图片超过媒体大小上限")
    try:
        return base64.b64decode(compact, validate=True)
    except ValueError, binascii.Error:
        raise ValueError("生图接口返回的 Base64 图片数据无效") from None


def _parse_data_url_b64(source: str) -> str:
    matched = _DATA_IMAGE_URL.match(source.strip())
    if matched is None:
        raise ValueError("生图接口返回的图片 data URL 格式无效")
    return matched.group(1)


def _ratio_from_dimensions(width: int, height: int) -> str | None:
    if width <= 0 or height <= 0:
        return None
    divisor = math.gcd(width, height)
    reduced = (width // divisor, height // divisor)
    if reduced in _KNOWN_RATIOS:
        return f"{reduced[0]}:{reduced[1]}"
    actual = width / height
    best: tuple[int, int] | None = None
    best_diff = 0.05
    for rw, rh in _KNOWN_RATIOS:
        diff = abs(actual - rw / rh) / (rw / rh)
        if diff <= best_diff:
            best_diff = diff
            best = (rw, rh)
    if best is not None:
        return f"{best[0]}:{best[1]}"
    return None


def _resolve_aspect_ratio(request: GenerateImageInput) -> str | None:
    if request.aspect_ratio is not None:
        raw = request.aspect_ratio.strip()
        if raw and raw.lower() != "auto":
            if matched := _ASPECT_RATIO.match(raw):
                return f"{matched.group(1)}:{matched.group(2)}"
            return raw
    if request.size is not None:
        raw = request.size.strip()
        if matched := _ASPECT_RATIO.match(raw):
            return f"{matched.group(1)}:{matched.group(2)}"
        if dims := _DIMENSIONS.match(raw):
            return _ratio_from_dimensions(int(dims.group(1)), int(dims.group(2)))
    return None


def _resolve_gemini_image_size(size: str | None) -> str | None:
    if size is None:
        return None
    raw = size.strip()
    if _RESOLUTION_TIER.match(raw):
        return raw.upper()
    return None


def _resolve_xai_resolution(size: str | None) -> str | None:
    if size is None:
        return None
    raw = size.strip()
    lower = raw.lower()
    if lower in ("1k", "2k"):
        return lower
    if dims := _DIMENSIONS.match(raw):
        if max(int(dims.group(1)), int(dims.group(2))) >= 2048:
            return "2k"
    return None


def _dashscope_size(size: str | None) -> str | None:
    if size is None:
        return None
    if dims := _DIMENSIONS.match(size.strip()):
        return f"{dims.group(1)}*{dims.group(2)}"
    return None


def _gemini_model_path(model: str) -> str:
    cleaned = model.strip().removeprefix("models/")
    if (
        not cleaned
        or not _GEMINI_MODEL_ID.fullmatch(cleaned)
        or any(segment in (".", "..") for segment in cleaned.split("/"))
    ):
        raise _ImageRequestError("Gemini 生图模型名称包含非法字符", retryable=False)
    return cleaned


def _image_error_metadata(data: JsonObject) -> str:
    parts: list[str] = []
    if base := remote_error_metadata(data):
        parts.append(base)
    error = data.get("error")
    if isinstance(error, dict):
        status = error.get("status")
        if isinstance(status, str) and status in _KNOWN_ERROR_CODES:
            parts.append(status)
    code = data.get("code")
    if isinstance(code, str) and code in _KNOWN_ERROR_CODES:
        parts.append(code)
    output = data.get("output")
    if isinstance(output, dict):
        out_code = output.get("code")
        if isinstance(out_code, str) and out_code in _KNOWN_ERROR_CODES:
            parts.append(out_code)
    return "；".join(dict.fromkeys(parts))


def _is_permanent_image_error(data: JsonObject) -> bool:
    if permanent_request_error(data):
        return True
    error = data.get("error")
    if isinstance(error, dict):
        status = error.get("status")
        if isinstance(status, str) and status in _PERMANENT_STATUS_CODES:
            return True
    code = data.get("code")
    if isinstance(code, str) and code in _PERMANENT_STATUS_CODES:
        return True
    output = data.get("output")
    if isinstance(output, dict):
        out_code = output.get("code")
        if isinstance(out_code, str) and out_code in _PERMANENT_STATUS_CODES:
            return True
    return False


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
        default=None, min_length=1, description="引用共享工作区内的图片相对路径"
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
        description="参考图列表；为空时文生图，非空时结合参考图改图或图生图",
    )
    n: int = Field(default=1, ge=1, le=10, description="生成图片张数")
    size: str | None = Field(
        default=None,
        min_length=1,
        description="图片尺寸或分辨率档位，如 1024x1024、1536x1024、1K、2K、4K、auto；省略时由接口默认",
    )
    aspect_ratio: str | None = Field(
        default=None,
        min_length=1,
        description="画面宽高比，如 1:1、16:9、9:16、4:3、3:4、3:2、2:3；省略时由接口默认",
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
        description="可选：将生成的图片同时保存到共享工作区的相对路径",
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
        generated = context.generated_media.get(ref.generated_id)
        if generated is None:
            raise ValueError(f"参考图 generated_id ({ref.generated_id}) 不存在或已过期")
        if not generated.mime_type.startswith("image/"):
            raise ValueError(f"参考图 generated_id ({ref.generated_id}) 不是图片")
        mime, ext = detect_image_format(generated.content)
        return generated.content, mime, ext
    if ref.workspace_path is not None:
        denied = context.check_tool(
            "workspace__read_file", {"path": ref.workspace_path, "offset": 0, "limit": 65_536}
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


async def _send_protocol_request(
    client: httpx.AsyncClient,
    config: ImageSettings,
    request: GenerateImageInput,
    references: list[tuple[bytes, str, str]],
) -> httpx.Response:
    base = config.endpoint_base()
    headers = config.request_headers()
    protocol = config.protocol

    if protocol == "openai_images":
        optional_fields = (
            ("size", request.size),
            ("aspect_ratio", request.aspect_ratio),
            ("quality", request.quality),
            ("background", request.background),
            ("output_format", request.output_format),
            ("style", request.style),
            ("response_format", request.response_format),
        )
        if not references:
            body: JsonObject = {
                "model": config.model,
                "prompt": request.prompt,
                "n": request.n,
            }
            for key, value in optional_fields:
                if value is not None:
                    body[key] = value
            return await client.post(
                f"{base}/images/generations",
                headers=headers,
                json=body,
                timeout=config.timeout,
            )
        form_data: dict[str, str] = {
            "model": config.model,
            "prompt": request.prompt,
            "n": str(request.n),
        }
        for key, value in optional_fields:
            if value is not None:
                form_data[key] = str(value)
        multipart_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
        field_name = "image" if len(references) == 1 else "image[]"
        files = [
            (field_name, (f"image_{index}.{ext}", content, mime))
            for index, (content, mime, ext) in enumerate(references)
        ]
        return await client.post(
            f"{base}/images/edits",
            headers=multipart_headers,
            data=form_data,
            files=files,
            timeout=config.timeout,
        )

    if protocol == "chat_completions":
        if not references:
            content_value: JsonValue = request.prompt
        else:
            parts: list[JsonValue] = [{"type": "text", "text": request.prompt}]
            for ref_bytes, mime, _ext in references:
                parts.append(
                    {"type": "image_url", "image_url": {"url": _encode_data_url(ref_bytes, mime)}}
                )
            content_value = parts
        body = {
            "model": config.model,
            "stream": False,
            "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": content_value}],
        }
        image_config: JsonObject = {}
        if ratio := _resolve_aspect_ratio(request):
            image_config["aspect_ratio"] = ratio
        if image_size := _resolve_gemini_image_size(request.size):
            image_config["image_size"] = image_size
        if image_config:
            body["image_config"] = image_config
        return await client.post(
            f"{base}/chat/completions",
            headers=headers,
            json=body,
            timeout=config.timeout,
        )

    if protocol == "responses":
        if not references:
            input_value: JsonValue = request.prompt
        else:
            input_parts: list[JsonValue] = [{"type": "input_text", "text": request.prompt}]
            for ref_bytes, mime, _ext in references:
                input_parts.append(
                    {"type": "input_image", "image_url": _encode_data_url(ref_bytes, mime)}
                )
            input_value = [{"role": "user", "content": input_parts}]
        tool_spec: JsonObject = {
            "type": "image_generation",
            "action": "edit" if references else "generate",
        }
        for key, value in (
            ("size", request.size),
            ("quality", request.quality),
            ("background", request.background),
            ("output_format", request.output_format),
        ):
            if value is not None:
                tool_spec[key] = value
        body = {
            "model": config.model,
            "stream": False,
            "store": False,
            "input": input_value,
            "tools": [tool_spec],
            "tool_choice": {"type": "image_generation"},
        }
        return await client.post(
            f"{base}/responses",
            headers=headers,
            json=body,
            timeout=config.timeout,
        )

    if protocol == "gemini":
        model_path = _gemini_model_path(config.model)
        gemini_parts: list[JsonValue] = [{"text": request.prompt}]
        for ref_bytes, mime, _ext in references:
            gemini_parts.append(
                {
                    "inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(ref_bytes).decode("ascii"),
                    }
                }
            )
        gen_config: JsonObject = {"responseModalities": ["TEXT", "IMAGE"]}
        image_cfg: JsonObject = {}
        if ratio := _resolve_aspect_ratio(request):
            image_cfg["aspectRatio"] = ratio
        if image_size := _resolve_gemini_image_size(request.size):
            image_cfg["imageSize"] = image_size
        if image_cfg:
            gen_config["imageConfig"] = image_cfg
        body = {
            "contents": [{"role": "user", "parts": gemini_parts}],
            "generationConfig": gen_config,
        }
        return await client.post(
            f"{base}/models/{model_path}:generateContent",
            headers=headers,
            json=body,
            timeout=config.timeout,
        )

    if protocol == "xai_images":
        body = {
            "model": config.model,
            "prompt": request.prompt,
            "n": request.n,
        }
        if ratio := _resolve_aspect_ratio(request):
            body["aspect_ratio"] = ratio
        if resolution := _resolve_xai_resolution(request.size):
            body["resolution"] = resolution
        if request.quality is not None:
            body["quality"] = request.quality
        if request.response_format is not None:
            body["response_format"] = request.response_format
        if not references:
            return await client.post(
                f"{base}/images/generations",
                headers=headers,
                json=body,
                timeout=config.timeout,
            )
        if len(references) == 1:
            ref_bytes, mime, _ext = references[0]
            body["image"] = {"type": "image_url", "url": _encode_data_url(ref_bytes, mime)}
        else:
            body["images"] = [
                {"type": "image_url", "url": _encode_data_url(ref_bytes, mime)}
                for ref_bytes, mime, _ext in references
            ]
        return await client.post(
            f"{base}/images/edits",
            headers=headers,
            json=body,
            timeout=config.timeout,
        )

    if protocol == "ark_images":
        body = {
            "model": config.model,
            "prompt": request.prompt,
        }
        if len(references) == 1:
            ref_bytes, mime, _ext = references[0]
            body["image"] = _encode_data_url(ref_bytes, mime)
        elif len(references) > 1:
            body["image"] = [
                _encode_data_url(ref_bytes, mime) for ref_bytes, mime, _ext in references
            ]
        if request.size is not None:
            body["size"] = request.size
        if request.response_format is not None:
            body["response_format"] = request.response_format
        return await client.post(
            f"{base}/images/generations",
            headers=headers,
            json=body,
            timeout=config.timeout,
        )

    # dashscope 原生同步多模态生图 / 改图协议
    ds_content: list[JsonValue] = [
        {"image": _encode_data_url(ref_bytes, mime)} for ref_bytes, mime, _ext in references
    ]
    ds_content.append({"text": request.prompt})
    body = {
        "model": config.model,
        "input": {"messages": [{"role": "user", "content": ds_content}]},
    }
    parameters: JsonObject = {}
    if request.n > 1:
        parameters["n"] = request.n
    if ds_size := _dashscope_size(request.size):
        parameters["size"] = ds_size
    if parameters:
        body["parameters"] = parameters
    endpoint = (
        base
        if base.endswith("/services/aigc/multimodal-generation/generation")
        else f"{base}/services/aigc/multimodal-generation/generation"
    )
    return await client.post(
        endpoint,
        headers=headers,
        json=body,
        timeout=config.timeout,
    )


async def _post_image_request(
    client: httpx.AsyncClient,
    config: ImageSettings,
    request: GenerateImageInput,
    references: list[tuple[bytes, str, str]],
) -> JsonObject:
    try:
        async with asyncio.timeout(config.timeout):
            response = await _send_protocol_request(client, config, request, references)
        if response.is_error:
            error_obj: JsonObject = {}
            if len(response.content) <= 65_536:
                try:
                    error_obj = JSON_OBJECT.validate_json(response.content)
                except ValueError:
                    error_obj = {}
            details = _image_error_metadata(error_obj)
            endpoint_label = protocol_endpoint_label(
                config.protocol, has_references=bool(references)
            )
            if response.status_code in (400, 404, 405, 422):
                message = (
                    f"生图接口返回 HTTP {response.status_code}"
                    f"（协议 {config.protocol}，端点 {endpoint_label}）"
                    + (f"；{details}" if details else "")
                    + "；请确认当前模型与所选接口协议匹配"
                )
            else:
                message = f"生图接口返回 HTTP {response.status_code}（协议 {config.protocol}）" + (
                    f"；{details}" if details else ""
                )
            raise _ImageRequestError(
                message,
                retryable=(response.status_code in (408, 409, 429) or response.status_code >= 500)
                and not _is_permanent_image_error(error_obj),
                retry_after=retry_after_seconds(response.headers.get("retry-after")),
            )
        payload = JSON_OBJECT.validate_json(response.content)
        if payload.get("error"):
            details = _image_error_metadata(payload)
            raise _ImageRequestError(
                "生图接口返回错误" + (f"；{details}" if details else ""),
                retryable=not _is_permanent_image_error(payload),
                retry_after=retry_after_seconds(response.headers.get("retry-after")),
            )
        if config.protocol == "responses" and payload.get("status") == "failed":
            details = _image_error_metadata(payload)
            raise _ImageRequestError(
                "生图响应执行失败" + (f"；{details}" if details else ""),
                retryable=not _is_permanent_image_error(payload),
            )
        if config.protocol == "dashscope":
            output = payload.get("output")
            has_top_code = isinstance(payload.get("code"), str) and bool(payload.get("code"))
            failed_task = isinstance(output, dict) and output.get("task_status") == "FAILED"
            if (has_top_code and not isinstance(output, dict)) or failed_task:
                details = _image_error_metadata(payload)
                raise _ImageRequestError(
                    "生图接口返回错误" + (f"；{details}" if details else ""),
                    retryable=not _is_permanent_image_error(payload),
                )
        return payload
    except _ImageRequestError:
        raise
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


def _extract_image_entry(entry: JsonValue) -> _ExtractedImage | None:
    if isinstance(entry, str) and entry.strip():
        return _ExtractedImage(url=entry.strip())
    if not isinstance(entry, dict):
        return None
    revised = entry.get("revised_prompt")
    revised_prompt = revised if isinstance(revised, str) and revised else None

    b64 = entry.get("b64_json") or entry.get("b64")
    if isinstance(b64, str) and b64:
        return _ExtractedImage(b64=b64, revised_prompt=revised_prompt)

    image_url = entry.get("image_url")
    if isinstance(image_url, str) and image_url.strip():
        return _ExtractedImage(url=image_url.strip(), revised_prompt=revised_prompt)
    if isinstance(image_url, dict):
        nested_url = image_url.get("url")
        if isinstance(nested_url, str) and nested_url.strip():
            return _ExtractedImage(url=nested_url.strip(), revised_prompt=revised_prompt)

    source = entry.get("source")
    if isinstance(source, dict):
        source_data = source.get("data")
        if isinstance(source_data, str) and source_data:
            return _ExtractedImage(b64=source_data, revised_prompt=revised_prompt)
        source_url = source.get("url")
        if isinstance(source_url, str) and source_url.strip():
            return _ExtractedImage(url=source_url.strip(), revised_prompt=revised_prompt)

    inline = entry.get("inlineData") or entry.get("inline_data")
    if isinstance(inline, dict):
        inline_data = inline.get("data")
        if isinstance(inline_data, str) and inline_data:
            return _ExtractedImage(b64=inline_data, revised_prompt=revised_prompt)

    url = entry.get("url") or entry.get("image")
    if isinstance(url, str) and url.strip():
        return _ExtractedImage(url=url.strip(), revised_prompt=revised_prompt)
    return None


def _extract_openai_images(payload: JsonObject) -> list[_ExtractedImage]:
    raw_items = payload.get("data")
    if not isinstance(raw_items, list):
        return []
    extracted: list[_ExtractedImage] = []
    for entry in raw_items:
        item = _extract_image_entry(entry)
        if item is not None:
            extracted.append(item)
    return extracted


def _extract_chat_images(payload: JsonObject) -> list[_ExtractedImage]:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return []
    extracted: list[_ExtractedImage] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        found_structured = False
        images = message.get("images")
        if isinstance(images, list):
            for entry in images:
                item = _extract_image_entry(entry)
                if item is not None:
                    extracted.append(item)
                    found_structured = True
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type in ("image_url", "image", "output_image") or (
                    "inlineData" in part or "inline_data" in part
                ):
                    item = _extract_image_entry(part)
                    if item is not None:
                        extracted.append(item)
                        found_structured = True
        elif isinstance(content, str) and not found_structured:
            markdown_urls = [match.group(1).strip() for match in _MARKDOWN_IMAGE.finditer(content)]
            if markdown_urls:
                for url in markdown_urls:
                    extracted.append(_ExtractedImage(url=url))
            else:
                for match in _INLINE_DATA_IMAGE.finditer(content):
                    extracted.append(_ExtractedImage(url=match.group(0)))
    return extracted


def _extract_responses_images(payload: JsonObject) -> list[_ExtractedImage]:
    outputs = payload.get("output")
    if not isinstance(outputs, list):
        return []
    extracted: list[_ExtractedImage] = []
    for item in outputs:
        if not isinstance(item, dict) or item.get("type") != "image_generation_call":
            continue
        result = item.get("result")
        if not isinstance(result, str) or not result:
            continue
        revised = item.get("revised_prompt")
        extracted.append(
            _ExtractedImage(
                b64=result,
                revised_prompt=revised if isinstance(revised, str) and revised else None,
            )
        )
    return extracted


def _extract_gemini_images(payload: JsonObject) -> list[_ExtractedImage]:
    extracted: list[_ExtractedImage] = []
    block_reason: str | None = None
    prompt_feedback = payload.get("promptFeedback")
    if isinstance(prompt_feedback, dict):
        raw_block = prompt_feedback.get("blockReason")
        if isinstance(raw_block, str) and raw_block in _GEMINI_BLOCK_REASONS:
            block_reason = raw_block

    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            finish_reason = candidate.get("finishReason")
            if (
                block_reason is None
                and isinstance(finish_reason, str)
                and finish_reason in _GEMINI_BLOCK_REASONS
            ):
                block_reason = finish_reason
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict) or part.get("thought") is True:
                    continue
                inline = part.get("inlineData") or part.get("inline_data")
                if not isinstance(inline, dict):
                    continue
                data = inline.get("data")
                if isinstance(data, str) and data:
                    extracted.append(_ExtractedImage(b64=data))
    if not extracted and block_reason is not None:
        raise _ImageRequestError(f"生图请求被上游安全策略拦截（{block_reason}）", retryable=False)
    return extracted


def _extract_dashscope_images(payload: JsonObject) -> list[_ExtractedImage]:
    output = payload.get("output")
    if not isinstance(output, dict):
        return []
    extracted: list[_ExtractedImage] = []
    choices = output.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                image = part.get("image")
                if isinstance(image, str) and image.strip():
                    extracted.append(_ExtractedImage(url=image.strip()))
    if extracted:
        return extracted
    results = output.get("results")
    if isinstance(results, list):
        for entry in results:
            item = _extract_image_entry(entry)
            if item is not None:
                extracted.append(item)
    return extracted


def _extract_images(config: ImageSettings, payload: JsonObject) -> list[_ExtractedImage]:
    protocol = config.protocol
    if protocol in ("openai_images", "xai_images", "ark_images"):
        return _extract_openai_images(payload)
    if protocol == "chat_completions":
        return _extract_chat_images(payload)
    if protocol == "responses":
        return _extract_responses_images(payload)
    if protocol == "gemini":
        return _extract_gemini_images(payload)
    return _extract_dashscope_images(payload)


async def _decode_extracted_image(
    entry: _ExtractedImage,
    config: ImageSettings,
    media_reader: MediaReader,
    http_client: httpx.AsyncClient,
    max_bytes: int,
) -> tuple[bytes, str, str] | None:
    if entry.b64 is not None:
        content = _decode_base64_image(entry.b64, max_bytes)
    elif entry.url is not None:
        if entry.url.lower().startswith("data:"):
            content = _decode_base64_image(_parse_data_url_b64(entry.url), max_bytes)
        else:
            content = await _download_generated_url(entry.url, config, media_reader, http_client)
    else:
        return None
    if len(content) > max_bytes:
        raise ValueError("生成的图片超过媒体大小上限")
    mime, ext = detect_image_format(content)
    return content, mime, ext


async def preview_image(
    config: ImageSettings,
    prompt: str,
    *,
    size: str | None = None,
    aspect_ratio: str | None = None,
    napcat_settings: NapCatSettings | None = None,
    reader: MediaReader | None = None,
    client: httpx.AsyncClient | None = None,
) -> ImagePreviewResult | ToolResult:
    """按已保存的生图设置发起一次测试文生图；不依赖 enabled 开关，不重试，不落盘。"""
    if not config.endpoint_base():
        return ToolResult.failure("请先填写有效的生图接口地址", "InvalidConfiguration")
    if not config.model.strip():
        return ToolResult.failure("请先选择或填写生图模型名称", "InvalidConfiguration")
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return ToolResult.failure("测试提示词不能为空", "ValidationError")

    media_reader = reader or MediaReader(napcat_settings)
    request = GenerateImageInput(prompt=clean_prompt, size=size, aspect_ratio=aspect_ratio)
    owned_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=config.timeout, proxy=config.proxy, trust_env=False
    )
    started = time.monotonic()
    try:
        try:
            payload = await _post_image_request(http_client, config, request, [])
            extracted = _extract_images(config, payload)
        except _ImageRequestError as error:
            return ToolResult.failure(str(error), "ImageGenerationError")
        if not extracted:
            return ToolResult.failure("生图接口未返回有效图片数据", "ImageGenerationError")
        max_bytes = media_reader.settings.media_max_bytes
        for entry in extracted:
            try:
                decoded = await _decode_extracted_image(
                    entry, config, media_reader, http_client, max_bytes
                )
            except ValueError as exc:
                return ToolResult.failure(str(exc), "ImageGenerationError")
            if decoded is not None:
                content, mime, _ext = decoded
                return ImagePreviewResult(
                    content=content,
                    mime_type=mime,
                    protocol=config.protocol,
                    endpoint=protocol_endpoint_label(config.protocol, has_references=False),
                    elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
                    revised_prompt=entry.revised_prompt,
                )
        return ToolResult.failure("生图接口未返回可解析的图片内容", "ImageGenerationError")
    finally:
        if owned_client:
            await http_client.aclose()


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
                "workspace__write_file",
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
                return ToolResult.failure("工作区文件写入服务不可用", "WorkspaceError")

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
            decoded: list[tuple[bytes, str, str, str | None]] = []
            total_bytes = 0
            max_bytes = media_reader.settings.media_max_bytes
            max_rounds = request.n if config.protocol in _SINGLE_OUTPUT_PROTOCOLS else 1

            for round_index in range(max_rounds):
                if len(decoded) >= request.n:
                    break
                payload: JsonObject | None = None
                for retry in range(config.max_retries + 1):
                    if context.cancelled():
                        return ToolResult(status="cancelled", message="当前轮次已停止")
                    if denied := context.recheck():
                        return denied
                    try:
                        payload = await _post_image_request(
                            http_client, config, request, references
                        )
                        break
                    except _ImageRequestError as error:
                        if not error.retryable or retry == config.max_retries:
                            suffix = f"；已重试 {retry} 次" if retry else ""
                            return ToolResult.failure(f"{error}{suffix}", "ImageGenerationError")
                        await asyncio.sleep(retry_delay(retry + 1, error.retry_after))
                assert payload is not None

                try:
                    extracted = _extract_images(config, payload)
                except _ImageRequestError as error:
                    return ToolResult.failure(str(error), "ImageGenerationError")
                if not extracted:
                    if round_index == 0:
                        return ToolResult.failure(
                            "生图接口未返回有效图片数据", "ImageGenerationError"
                        )
                    break

                remaining = request.n - len(decoded)
                for entry in extracted[:remaining]:
                    if context.cancelled():
                        return ToolResult(status="cancelled", message="当前轮次已停止")
                    if denied := context.recheck():
                        return denied
                    try:
                        decoded_one = await _decode_extracted_image(
                            entry, config, media_reader, http_client, max_bytes
                        )
                        if decoded_one is None:
                            continue
                        content, mime, ext = decoded_one
                        if total_bytes + len(content) > max_bytes:
                            return ToolResult.failure(
                                "生成的图片超过媒体大小上限", "ImageGenerationError"
                            )
                    except ValueError as exc:
                        return ToolResult.failure(str(exc), "ImageGenerationError")
                    total_bytes += len(content)
                    decoded.append((content, mime, ext, entry.revised_prompt))

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
                remember_generated(context.generated_media, GeneratedMedia(gen_id, content, mime))
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
                "生成或编辑图片。不传 images 时按描述生成新图；传入 images 时结合参考图改图或图生图"
                "（可引用当前会话消息图片、QQ 头像、群头像、官方图床链接、工作区图片或此前生成的 generated_id）。"
                "返回的 generated_id 可直接传给 qq__send_message 或头像修改工具发送。"
            ),
            "image",
            model_binding(GenerateImageInput, generate),
            title="生成或编辑图片",
        )
    ]
