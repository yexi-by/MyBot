"""本机 IndexTTS 2.5 语音合成与远端管理：支持零样本音色库管理、模型显存控制与多模式情感合成。"""

import asyncio
import base64
import binascii
import io
import logging
import re
import wave
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from .providers import retry_after_seconds, retry_delay
from .settings import NapCatSettings, TTSSettings

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {"tts": "语音合成"}

VOICE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_VOICE_ID = re.compile(VOICE_PATTERN)
_LIST_LIMIT = 4 * 1024 * 1024
_SAMPLE_LIMIT = 16 * 1024 * 1024
_NAME_LIMIT = 100

EmotionMode = Literal["reference", "text", "vector", "audio"]
SpeechLanguage = Literal["zh", "en", "ja", "es", "ar"]

# 本机 IndexTTS 服务返回的受控错误码白名单；不回显未过滤的服务端原始错误文本。
_ERROR_CODES = {
    "unauthorized": "连接密钥无效，请检查语音合成 API Key",
    "voice_not_found": "所选音色不存在，请在语音合成页重新选择",
    "emotion_voice_not_found": "所选情绪参考音色不存在",
    "voice_exists": "同名音色 ID 已存在，请更换音色 ID",
    "invalid_voice_id": "音色 ID 格式无效",
    "invalid_audio": "音频格式无效或已损坏，请上传有效的音频文件",
    "empty_text": "合成文本不能为空",
    "invalid_emotion": "情绪控制参数无效",
    "invalid_request": "语音请求参数无效",
    "model_not_loaded": "本机 IndexTTS 模型尚未加载或加载失败",
    "model_busy": "本机 IndexTTS 正在处理其他任务，请稍后重试",
    "synthesis_failed": "本机 IndexTTS 推理失败",
}

_RETRYABLE_TRANSPORT = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
)


class EmotionVector(Model):
    """IndexTTS 2.5 的 8 维基础情感向量（0.0~1.0），支持单一情绪或复合情绪配比。"""

    happy: float = Field(default=0.0, ge=0.0, le=1.0, description="开心/元气强度（0~1）")
    angry: float = Field(default=0.0, ge=0.0, le=1.0, description="生气/恼怒强度（0~1）")
    sad: float = Field(default=0.0, ge=0.0, le=1.0, description="悲伤/委屈强度（0~1）")
    afraid: float = Field(default=0.0, ge=0.0, le=1.0, description="害怕/惊慌强度（0~1）")
    disgusted: float = Field(default=0.0, ge=0.0, le=1.0, description="厌恶/嫌弃强度（0~1）")
    melancholic: float = Field(default=0.0, ge=0.0, le=1.0, description="低落/忧郁强度（0~1）")
    surprised: float = Field(default=0.0, ge=0.0, le=1.0, description="惊讶/意外强度（0~1）")
    calm: float = Field(default=0.0, ge=0.0, le=1.0, description="平静/淡定强度（0~1）")

    def as_list(self) -> list[float]:
        return [
            self.happy,
            self.angry,
            self.sad,
            self.afraid,
            self.disgusted,
            self.melancholic,
            self.surprised,
            self.calm,
        ]

    @model_validator(mode="after")
    def non_zero(self) -> EmotionVector:
        if not any(value > 0.0 for value in self.as_list()):
            raise ValueError("情感向量至少需要一个维度的强度大于 0")
        return self


class SynthesizeSpeech(Model):
    text: str = Field(
        min_length=1,
        max_length=2000,
        description="要读出的台词原文，逐字朗读",
    )
    language: SpeechLanguage = Field(
        default="zh",
        description="台词语言：zh 中文（默认）、en 英文、ja 日文、es 西班牙文、ar 阿拉伯文",
    )
    emotion_mode: EmotionMode | None = Field(
        default=None,
        description=(
            "情绪控制方式，由你根据台词情境自由选择："
            "reference（默认，跟随音色原声的自然语气）、"
            "text（用 emotion_text 自然语言描述情绪与语气）、"
            "vector（用 emotion_vector 8 维情感向量配比复合情绪）、"
            "audio（用 emotion_voice 借用指定参考音频的情绪语调）。"
            "省略时按实际填写的 emotion_* 字段自动选择"
        ),
    )
    emotion_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="text 模式使用：自然语言情绪或语气描述，如“傲娇、嘴硬心软”“委屈快哭出来”“元气兴奋”",
    )
    emotion_vector: EmotionVector | None = Field(
        default=None,
        description="vector 模式使用：8 维基础情绪强度配比（0~1），支持复合情绪（如 angry+sad 表现委屈生气）",
    )
    emotion_voice: str | None = Field(
        default=None,
        pattern=VOICE_PATTERN,
        description="audio 模式使用：作为情绪来源的音色/参考音频 ID（仅借用其情绪起伏，不改变你的说话音色）",
    )
    emotion_alpha: float = Field(
        default=1.0,
        ge=0.0,
        le=1.5,
        description="情绪强度权重（0.0~1.5，默认 1.0），数值越大情绪表现越强烈",
    )
    workspace_path: str | None = Field(
        default=None,
        min_length=1,
        description="可选：同时把 WAV 保存到共享沙箱工作区的相对路径",
    )

    @model_validator(mode="after")
    def check_emotion_fields(self) -> SynthesizeSpeech:
        provided = sum(
            (
                self.emotion_text is not None,
                self.emotion_vector is not None,
                self.emotion_voice is not None,
            )
        )
        if provided > 1:
            raise ValueError("emotion_text、emotion_vector、emotion_voice 只能选择一种填写")
        mode = self.emotion_mode
        if mode == "reference" and provided > 0:
            raise ValueError(
                "emotion_mode 为 reference 时不能同时填写 emotion_text、emotion_vector 或 emotion_voice"
            )
        if mode == "text" and self.emotion_text is None:
            raise ValueError("emotion_mode 为 text 时必须填写 emotion_text")
        if mode == "vector" and self.emotion_vector is None:
            raise ValueError("emotion_mode 为 vector 时必须填写 emotion_vector")
        if mode == "audio" and self.emotion_voice is None:
            raise ValueError("emotion_mode 为 audio 时必须填写 emotion_voice")
        return self

    def resolved_mode(self) -> EmotionMode:
        if self.emotion_mode is not None:
            return self.emotion_mode
        if self.emotion_text is not None:
            return "text"
        if self.emotion_vector is not None:
            return "vector"
        if self.emotion_voice is not None:
            return "audio"
        return "reference"

    def request_payload(self, voice: str) -> JsonObject:
        mode = self.resolved_mode()
        payload: JsonObject = {
            "voice": voice,
            "text": self.text,
            "language": self.language,
            "emotion_mode": mode,
            "emotion_alpha": self.emotion_alpha,
        }
        if mode == "text" and self.emotion_text is not None:
            payload["emotion_text"] = self.emotion_text
        elif mode == "vector" and self.emotion_vector is not None:
            payload["emotion_vector"] = [val for val in self.emotion_vector.as_list()]
        elif mode == "audio" and self.emotion_voice is not None:
            payload["emotion_voice"] = self.emotion_voice
        return payload


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    duration: float


@dataclass(frozen=True)
class CustomVoice:
    """本机 IndexTTS 音色库中的参考音频条目。"""

    id: str
    name: str
    duration: float = 0.0


class _SpeechError(RuntimeError):
    def __init__(
        self,
        message: str,
        kind: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        http_status: int = 0,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.retry_after = retry_after
        self.http_status = http_status


def _text(data: JsonObject, *names: str) -> str:
    for name in names:
        value = data.get(name)
        if isinstance(value, str):
            return value
    return ""


def _error_payload(response: httpx.Response) -> JsonObject:
    if len(response.content) > 65_536:
        return {}
    try:
        return JSON_OBJECT.validate_json(response.content)
    except ValueError:
        return {}


def _error_hint(payload: JsonObject, status: int) -> str:
    code = payload.get("error") or payload.get("code")
    if isinstance(code, str) and code in _ERROR_CODES:
        return f"{code}：{_ERROR_CODES[code]}"
    error_obj = payload.get("error")
    if isinstance(error_obj, dict):
        nested = error_obj.get("code") or error_obj.get("type")
        if isinstance(nested, str) and nested in _ERROR_CODES:
            return f"{nested}：{_ERROR_CODES[nested]}"
    if status == 401 or status == 403:
        return _ERROR_CODES["unauthorized"]
    if status == 404:
        return "接口路径或请求的音色不存在"
    return ""


def _is_permanent_code(payload: JsonObject) -> bool:
    code = payload.get("error") or payload.get("code")
    if isinstance(code, dict):
        code = code.get("code") or code.get("type")
    return isinstance(code, str) and code in {
        "unauthorized",
        "voice_not_found",
        "emotion_voice_not_found",
        "voice_exists",
        "invalid_voice_id",
        "invalid_audio",
        "empty_text",
        "invalid_emotion",
    }


def _transport_error(error: httpx.HTTPError, label: str) -> _SpeechError:
    return _SpeechError(
        f"{label}连接失败（{type(error).__name__}）",
        "transport",
        retryable=isinstance(error, _RETRYABLE_TRANSPORT),
    )


@asynccontextmanager
async def _session(
    config: TTSSettings, client: httpx.AsyncClient | None
) -> AsyncIterator[httpx.AsyncClient]:
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(
        timeout=config.timeout, proxy=config.proxy, trust_env=False
    ) as owned:
        yield owned


def _endpoint(config: TTSSettings) -> str | ToolResult:
    base = config.endpoint_base()
    if not base:
        return ToolResult.failure("语音合成尚未填写有效的服务地址", "SpeechSynthesisError")
    return base


async def _call_json(
    client: httpx.AsyncClient,
    config: TTSSettings,
    method: str,
    url: str,
    label: str,
    *,
    limit: int,
    oversize: str = "",
    body: JsonObject | None = None,
) -> JsonObject:
    """请求本机 IndexTTS 接口并解析 JSON；错误仅保留状态码与受控枚举提示。"""
    try:
        async with asyncio.timeout(config.timeout):
            response = await client.request(
                method,
                url,
                headers=config.request_headers(),
                json=body,
                timeout=config.timeout,
            )
    except TimeoutError:
        raise _SpeechError(f"{label}请求超时", "timeout", retryable=True) from None
    except httpx.HTTPError as error:
        raise _transport_error(error, label) from None
    if response.is_error:
        status = response.status_code
        payload = _error_payload(response)
        hint = _error_hint(payload, status)
        raise _SpeechError(
            f"{label}返回 HTTP {status}" + (f"；{hint}" if hint else ""),
            "http",
            retryable=(status in (408, 429) or status >= 500) and not _is_permanent_code(payload),
            retry_after=retry_after_seconds(response.headers.get("retry-after")),
            http_status=status,
        )
    if len(response.content) > limit:
        raise _SpeechError(oversize or f"{label}返回的数据超过大小上限", "too_large")
    if not response.content.strip():
        return {}
    try:
        return JSON_OBJECT.validate_json(response.content)
    except ValueError:
        raise _SpeechError(
            f"{label}响应不是有效 JSON", "invalid_response", retryable=True
        ) from None


async def _call_audio(
    client: httpx.AsyncClient,
    config: TTSSettings,
    method: str,
    url: str,
    label: str,
    *,
    max_bytes: int,
    oversize: str = "",
    body: JsonObject | None = None,
) -> SpeechAudio:
    """请求音频接口，支持直接返回 audio/wav 二进制流或 JSON base64 封装。"""
    try:
        async with asyncio.timeout(config.timeout):
            response = await client.request(
                method,
                url,
                headers=config.request_headers(),
                json=body,
                timeout=config.timeout,
            )
    except TimeoutError:
        raise _SpeechError(f"{label}请求超时", "timeout", retryable=True) from None
    except httpx.HTTPError as error:
        raise _transport_error(error, label) from None
    if response.is_error:
        status = response.status_code
        payload = _error_payload(response)
        hint = _error_hint(payload, status)
        raise _SpeechError(
            f"{label}返回 HTTP {status}" + (f"；{hint}" if hint else ""),
            "http",
            retryable=(status in (408, 429) or status >= 500) and not _is_permanent_code(payload),
            retry_after=retry_after_seconds(response.headers.get("retry-after")),
            http_status=status,
        )
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type or response.content[:1] == b"{":
        if len(response.content) > max_bytes * 4 // 3 + 1_048_576:
            raise _SpeechError(oversize or "合成的语音超过媒体大小上限，请缩短文本", "too_large")
        try:
            payload = JSON_OBJECT.validate_json(response.content)
        except ValueError:
            raise _SpeechError(
                f"{label}响应不是有效 JSON", "invalid_response", retryable=True
            ) from None
        encoded = _text(payload, "audio", "data")
        if not encoded:
            raise _SpeechError(f"{label}没有返回音频数据", "empty", retryable=True)
        if len(encoded) > max_bytes * 4 // 3 + 16:
            raise _SpeechError(oversize or "合成的语音超过媒体大小上限，请缩短文本", "too_large")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except binascii.Error:
            raise _SpeechError(
                f"{label}返回的音频编码无效", "invalid_audio", retryable=True
            ) from None
        mime = _text(payload, "mime_type", "mimeType") or "audio/wav"
        wav, duration = normalize_wav(raw, mime)
    else:
        if len(response.content) > max_bytes:
            raise _SpeechError(oversize or "合成的语音超过媒体大小上限，请缩短文本", "too_large")
        wav, duration = normalize_wav(response.content, content_type or "audio/wav")
    if len(wav) > max_bytes:
        raise _SpeechError(oversize or "合成的语音超过媒体大小上限，请缩短文本", "too_large")
    return SpeechAudio(wav, duration)


async def _attempt[T](
    operation: Callable[[], Awaitable[T]],
    *,
    kind: str,
    retries: int,
    checkpoint: Callable[[], ToolResult | None] = lambda: None,
) -> T | ToolResult:
    """按重试设置执行一次逻辑请求；每次请求前经 checkpoint 检查取消与授权。"""
    retry = 0
    while True:
        if stopped := checkpoint():
            return stopped
        try:
            return await operation()
        except _SpeechError as error:
            if not error.retryable or retry >= retries:
                logger.warning(
                    "tts_request_failed",
                    extra={
                        "request_kind": kind,
                        "retry": retry,
                        "max_retries": retries,
                        "http_status": error.http_status,
                        "error_type": error.kind,
                    },
                )
                suffix = f"；已重试 {retry} 次" if retry else ""
                return ToolResult.failure(f"{error}{suffix}", "SpeechSynthesisError")
            retry += 1
            delay = retry_delay(retry, error.retry_after)
            logger.warning(
                "tts_request_retry",
                extra={
                    "request_kind": kind,
                    "retry": retry,
                    "max_retries": retries,
                    "delay_seconds": delay,
                    "http_status": error.http_status,
                    "error_type": error.kind,
                },
            )
            await asyncio.sleep(delay)


def normalize_wav(content: bytes, mime_type: str = "audio/wav") -> tuple[bytes, float]:
    """返回完整 WAV 与时长；接口给出裸 PCM（audio/L16）时按声明的采样率补上 WAV 头。"""
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(content), "rb") as reader:
                frames, rate = reader.getnframes(), reader.getframerate()
        except wave.Error, EOFError:
            raise _SpeechError("接口返回的 WAV 数据无效", "invalid_audio", retryable=True) from None
        if frames <= 0 or rate <= 0:
            raise _SpeechError("接口返回了空音频", "invalid_audio", retryable=True)
        return content, frames / rate
    kind, _, parameters = mime_type.partition(";")
    if kind.strip().lower() not in ("audio/l16", "audio/pcm"):
        raise _SpeechError("接口返回了不支持的音频格式", "invalid_audio")
    rate, channels = 24_000, 1
    for item in parameters.split(";"):
        name, _, value = item.partition("=")
        if value.strip().isdigit() and int(value) > 0:
            if name.strip().lower() == "rate":
                rate = int(value)
            elif name.strip().lower() == "channels":
                channels = int(value)
    if not content or len(content) % (2 * channels):
        raise _SpeechError("接口返回的 PCM 音频不完整", "invalid_audio", retryable=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(content)
    return buffer.getvalue(), len(content) / (2 * channels * rate)


def _voice_entry(data: JsonValue) -> CustomVoice | None:
    """解析音色条目；ID 不符合格式的条目直接忽略，避免拼进请求路径。"""
    if not isinstance(data, dict):
        return None
    voice_id = _text(data, "id")
    if not _VOICE_ID.fullmatch(voice_id):
        return None
    name = " ".join(_text(data, "name", "display_name", "displayName").split())[:_NAME_LIMIT]
    raw_duration = data.get("duration")
    duration = (
        float(raw_duration) if isinstance(raw_duration, int | float) and raw_duration >= 0 else 0.0
    )
    return CustomVoice(voice_id, name or voice_id, round(duration, 2))


def _status_payload(config: TTSSettings, data: JsonObject) -> JsonObject:
    vram_alloc = data.get("vram_allocated_mb")
    vram_res = data.get("vram_reserved_mb")
    voices = data.get("voice_count")
    return {
        "enabled": config.enabled,
        "configured": True,
        "available": bool(data.get("available", True)),
        "reason": _text(data, "reason"),
        "model_version": _text(data, "model_version") or "IndexTTS-2.5",
        "loaded": bool(data.get("loaded", False)),
        "device": _text(data, "device") or "unknown",
        "bf16": bool(data.get("bf16", False)),
        "vram_allocated_mb": int(vram_alloc) if isinstance(vram_alloc, int | float) else None,
        "vram_reserved_mb": int(vram_res) if isinstance(vram_res, int | float) else None,
        "voice_count": int(voices) if isinstance(voices, int | float) and voices >= 0 else 0,
    }


async def service_status(
    config: TTSSettings, *, client: httpx.AsyncClient | None = None
) -> JsonObject:
    """读取本机 IndexTTS 服务的在线状态、GPU 显存与模型加载情况。"""
    base = config.endpoint_base()
    if not base:
        return {
            "enabled": config.enabled,
            "configured": False,
            "available": False,
            "reason": "尚未填写本机 IndexTTS 服务地址",
            "model_version": "",
            "loaded": False,
            "device": "",
            "bf16": False,
            "vram_allocated_mb": None,
            "vram_reserved_mb": None,
            "voice_count": 0,
        }
    try:
        async with _session(config, client) as http:
            payload = await _call_json(
                http,
                config,
                "GET",
                f"{base}/v1/status",
                "本机 IndexTTS 状态接口",
                limit=_LIST_LIMIT,
            )
            return _status_payload(config, payload)
    except _SpeechError as error:
        return {
            "enabled": config.enabled,
            "configured": True,
            "available": False,
            "reason": str(error),
            "model_version": "",
            "loaded": False,
            "device": "",
            "bf16": False,
            "vram_allocated_mb": None,
            "vram_reserved_mb": None,
            "voice_count": 0,
        }


async def manage_model(
    config: TTSSettings,
    action: Literal["load", "unload"],
    *,
    client: httpx.AsyncClient | None = None,
) -> JsonObject | ToolResult:
    """远程控制本机 IndexTTS 模型加载到显存或卸载释放显存。"""
    base = _endpoint(config)
    if isinstance(base, ToolResult):
        return base

    async def execute(http: httpx.AsyncClient) -> JsonObject:
        payload = await _call_json(
            http,
            config,
            "POST",
            f"{base}/v1/model",
            "本机模型控制接口",
            body={"action": action},
            limit=_LIST_LIMIT,
        )
        return _status_payload(config, payload)

    async with _session(config, client) as http:
        return await _attempt(lambda: execute(http), kind="tts_model", retries=0)


async def list_voices(
    config: TTSSettings, *, client: httpx.AsyncClient | None = None
) -> list[CustomVoice] | ToolResult:
    """列出本机 IndexTTS 音色库中的所有音色与参考音频。"""
    base = _endpoint(config)
    if isinstance(base, ToolResult):
        return base

    async def fetch(http: httpx.AsyncClient) -> list[CustomVoice]:
        payload = await _call_json(
            http,
            config,
            "GET",
            f"{base}/v1/voices",
            "音色列表接口",
            limit=_LIST_LIMIT,
        )
        entries = payload.get("voices")
        voices: list[CustomVoice] = []
        for entry in entries if isinstance(entries, list) else []:
            if (voice := _voice_entry(entry)) is not None:
                voices.append(voice)
        return voices

    async with _session(config, client) as http:
        return await _attempt(lambda: fetch(http), kind="tts_voice", retries=config.max_retries)


async def upload_voice(
    config: TTSSettings,
    name: str,
    audio: bytes,
    *,
    voice_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> CustomVoice | ToolResult:
    """上传一段参考干声到本机 IndexTTS 音色库。"""
    base = _endpoint(config)
    if isinstance(base, ToolResult):
        return base
    if voice_id is not None and not _VOICE_ID.fullmatch(voice_id):
        return ToolResult.failure("音色 ID 无效", "InvalidArguments")
    if not audio or len(audio) > _SAMPLE_LIMIT:
        return ToolResult.failure("参考音频大小无效或超过 16 MB 上限", "InvalidArguments")
    body: JsonObject = {
        "name": name,
        "audio": base64.b64encode(audio).decode("ascii"),
    }
    if voice_id:
        body["id"] = voice_id

    async def create(http: httpx.AsyncClient) -> CustomVoice:
        payload = await _call_json(
            http,
            config,
            "POST",
            f"{base}/v1/voices",
            "音色上传接口",
            body=body,
            limit=_LIST_LIMIT,
        )
        voice = _voice_entry(payload)
        if voice is None:
            raise _SpeechError("音色接口没有返回有效的音色 ID", "invalid_response")
        return voice

    async with _session(config, client) as http:
        return await _attempt(lambda: create(http), kind="tts_voice", retries=0)


async def voice_sample(
    config: TTSSettings,
    voice_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> SpeechAudio | ToolResult:
    """读取本机音色库中指定音色的参考原声。"""
    if not _VOICE_ID.fullmatch(voice_id):
        return ToolResult.failure("音色 ID 无效", "InvalidArguments")
    base = _endpoint(config)
    if isinstance(base, ToolResult):
        return base

    async def fetch(http: httpx.AsyncClient) -> SpeechAudio:
        return await _call_audio(
            http,
            config,
            "GET",
            f"{base}/v1/voices/{voice_id}/sample",
            "音色原声接口",
            max_bytes=_SAMPLE_LIMIT,
        )

    async with _session(config, client) as http:
        return await _attempt(lambda: fetch(http), kind="tts_voice", retries=config.max_retries)


async def delete_voice(
    config: TTSSettings,
    voice_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ToolResult | None:
    """删除本机音色库中的指定音色。"""
    if not _VOICE_ID.fullmatch(voice_id):
        return ToolResult.failure("音色 ID 无效", "InvalidArguments")
    base = _endpoint(config)
    if isinstance(base, ToolResult):
        return base

    async def remove(http: httpx.AsyncClient) -> None:
        await _call_json(
            http,
            config,
            "DELETE",
            f"{base}/v1/voices/{voice_id}",
            "音色删除接口",
            limit=_LIST_LIMIT,
        )

    async with _session(config, client) as http:
        return await _attempt(lambda: remove(http), kind="tts_voice", retries=0)


async def synthesize_speech(
    config: TTSSettings,
    request: SynthesizeSpeech,
    voice: str | None = None,
    *,
    max_bytes: int,
    client: httpx.AsyncClient | None = None,
    checkpoint: Callable[[], ToolResult | None] = lambda: None,
) -> SpeechAudio | ToolResult:
    """含重试的完整语音合成；Agent 工具与控制台试听共用。"""
    base = _endpoint(config)
    if isinstance(base, ToolResult):
        return base
    selected_voice = voice or config.voice
    if not selected_voice or not _VOICE_ID.fullmatch(selected_voice):
        return ToolResult.failure("语音合成尚未选择有效音色", "SpeechSynthesisError")
    body = request.request_payload(selected_voice)

    async with _session(config, client) as http:
        return await _attempt(
            lambda: _call_audio(
                http,
                config,
                "POST",
                f"{base}/v1/synthesize",
                "语音合成接口",
                body=body,
                max_bytes=max_bytes,
                oversize="合成的语音超过媒体大小上限，请缩短文本",
            ),
            kind="tts",
            retries=config.max_retries,
            checkpoint=checkpoint,
        )


def build_tools(
    get_settings: Callable[[], TTSSettings],
    napcat_settings: NapCatSettings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[ToolDefinition]:
    max_bytes = (napcat_settings or NapCatSettings()).media_max_bytes

    async def synthesize(context: ToolContext, request: SynthesizeSpeech) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        config = get_settings()
        if reason := config.unavailable_reason():
            return ToolResult.failure(reason, "PermissionDenied")
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
        # 说话音色由管理员在配置中固定，Agent 不能更换；情绪控制方式由 Agent 自行选择。
        audio = await synthesize_speech(
            config,
            request,
            config.voice,
            max_bytes=max_bytes,
            client=client,
            checkpoint=context.recheck,
        )
        if isinstance(audio, ToolResult):
            return audio
        media_id = f"tts_{uuid4().hex[:12]}"
        remember_generated(
            context.generated_media, GeneratedMedia(media_id, audio.content, "audio/wav")
        )
        data: JsonObject = {
            "generated_id": media_id,
            "mime_type": "audio/wav",
            "size": len(audio.content),
            "duration_seconds": round(audio.duration, 2),
            "emotion_mode": request.resolved_mode(),
        }
        if request.workspace_path is not None:
            assert context.write_workspace is not None
            if denied := context.recheck():
                return denied
            await context.write_workspace(request.workspace_path, audio.content)
            data["workspace_path"] = request.workspace_path
        return ToolResult.success(data)

    return [
        ToolDefinition(
            "tts__synthesize",
            (
                "用你自己的声音说话（IndexTTS 2.5）：把文字合成为语音，返回的 generated_id 用 "
                "qq__send_message 的 {type: record, generated_id} 发送。要发语音时首选此工具；"
                "说话音色由管理员固定，不能更换，但你可以根据台词情境自由选择情绪控制方式（emotion_mode）："
                "reference（默认原声语气）、text（用 emotion_text 自然语言描述语气情绪）、"
                "vector（用 emotion_vector 8 维情感向量配比情绪）、audio（用 emotion_voice 借用参考音频情绪），"
                "并可通过 emotion_alpha 调节情绪强度。text 只写要读出的台词原文。"
                "QQ AI 语音（ai_voice）是 QQ 自带的角色声线，不是你的声音，仅在被明确要求时使用。"
            ),
            "tts",
            model_binding(SynthesizeSpeech, synthesize),
            title="合成语音",
        )
    ]
