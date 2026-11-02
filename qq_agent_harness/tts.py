"""Gemini TTS 语音合成：服务账号签发访问令牌，经 Vertex generateContent 生成 WAV。"""

import asyncio
import base64
import binascii
import io
import logging
import re
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

import httpx
import jwt
from pydantic import Field

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
from .diagnostics import google_error_metadata
from .providers import retry_after_seconds, retry_delay
from .settings import NapCatSettings, ServiceAccount, TTSSettings

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {"tts": "语音合成"}

TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TOKEN_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
# 断言有效期 1 小时；令牌提前 5 分钟换新，避免请求途中过期。
TOKEN_LIFETIME = 3600
TOKEN_MARGIN = 300
ENDPOINTS = {
    "global": "https://aiplatform.googleapis.com",
    "us": "https://aiplatform.us.rep.googleapis.com",
    "eu": "https://aiplatform.eu.rep.googleapis.com",
}
VOICE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
# 令牌接口的错误码与对应提示；其余错误只显示 HTTP 状态。
_TOKEN_ERRORS = {
    "invalid_grant": "服务账号密钥无效或已删除，也可能是服务器时间偏差过大",
    "invalid_client": "服务账号不可用或已被禁用",
    "unauthorized_client": "服务账号无权签发访问令牌",
    "invalid_scope": "访问令牌权限范围无效",
}
# 表示内容被安全策略拦截的结束原因；重试不会得到不同结果。
_BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
_ENUM = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_RETRYABLE_TRANSPORT = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
)


class SynthesizeSpeech(Model):
    text: str = Field(
        min_length=1,
        max_length=2000,
        description="要读出的原文，逐字朗读；可插入 <sigh>、<laugh>、<short pause> 等标签",
    )
    style: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="语气、情绪或语速，如“慵懒、俏皮”；省略时自然朗读",
    )
    voice: str | None = Field(
        default=None,
        pattern=VOICE_PATTERN,
        description="音色名，如 Kore、Puck、Leda、Charon；省略时使用默认音色",
    )
    workspace_path: str | None = Field(
        default=None,
        min_length=1,
        description="可选：同时把 WAV 保存到共享沙箱工作区的相对路径",
    )


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    duration: float
    # 输出达到接口上限被截断；音频仍可使用。
    truncated: bool = False


class _SpeechError(RuntimeError):
    def __init__(
        self,
        message: str,
        kind: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        http_status: int = 0,
        reauthenticate: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.retry_after = retry_after
        self.http_status = http_status
        self.reauthenticate = reauthenticate


def _enum(value: JsonValue) -> str:
    return value if isinstance(value, str) and _ENUM.fullmatch(value) else "未知原因"


def _error_payload(response: httpx.Response) -> JsonObject:
    if len(response.content) > 65_536:
        return {}
    try:
        return JSON_OBJECT.validate_json(response.content)
    except ValueError:
        return {}


def _transport_error(error: httpx.HTTPError, label: str) -> _SpeechError:
    return _SpeechError(
        f"{label}连接失败（{type(error).__name__}）",
        "transport",
        retryable=isinstance(error, _RETRYABLE_TRANSPORT),
    )


class AccessTokens:
    """按服务账号缓存访问令牌；并发调用等待同一次刷新，凭据变更后重新签发。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._credentials = ""
        self._token = ""
        self._deadline = 0.0

    def invalidate(self) -> None:
        self._token = ""

    async def get(
        self,
        client: httpx.AsyncClient,
        credentials: str,
        account: ServiceAccount,
        timeout: float,
    ) -> str:
        async with self._lock:
            if (
                self._token
                and self._credentials == credentials
                and time.monotonic() < self._deadline
            ):
                return self._token
            token, lifetime = await _exchange(client, account, timeout)
            self._credentials, self._token = credentials, token
            self._deadline = time.monotonic() + max(0, lifetime - TOKEN_MARGIN)
            return token


async def _exchange(
    client: httpx.AsyncClient, account: ServiceAccount, timeout: float
) -> tuple[str, int]:
    """用服务账号私钥签名的 JWT 断言换取 OAuth 访问令牌；令牌地址固定，不取自密钥文件。"""
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": account.client_email,
            "scope": TOKEN_SCOPE,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + TOKEN_LIFETIME,
        },
        account.private_key,
        algorithm="RS256",
        headers={"kid": account.private_key_id} if account.private_key_id else None,
    )
    try:
        async with asyncio.timeout(timeout):
            response = await client.post(
                TOKEN_URL,
                data={"grant_type": TOKEN_GRANT, "assertion": assertion},
                timeout=timeout,
            )
    except TimeoutError:
        raise _SpeechError("访问令牌请求超时", "timeout", retryable=True) from None
    except httpx.HTTPError as error:
        raise _transport_error(error, "访问令牌接口") from None
    if response.is_error:
        code = _error_payload(response).get("error")
        hint = _TOKEN_ERRORS.get(code) if isinstance(code, str) else None
        status = response.status_code
        raise _SpeechError(
            f"访问令牌接口返回 HTTP {status}" + (f"；{code}：{hint}" if hint else ""),
            "token",
            retryable=status in (408, 429) or status >= 500,
            retry_after=retry_after_seconds(response.headers.get("retry-after")),
            http_status=status,
        )
    try:
        payload = JSON_OBJECT.validate_json(response.content)
    except ValueError:
        raise _SpeechError("访问令牌响应不是有效 JSON", "token", retryable=True) from None
    token = payload.get("access_token")
    lifetime = payload.get("expires_in")
    if not isinstance(token, str) or not token:
        raise _SpeechError("访问令牌响应缺少 access_token", "token", retryable=True)
    return token, lifetime if isinstance(lifetime, int) and lifetime > 0 else TOKEN_LIFETIME


def normalize_wav(content: bytes, mime_type: str) -> tuple[bytes, float]:
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


def _audio(payload: JsonObject, max_bytes: int) -> SpeechAudio:
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise _SpeechError(f"内容被安全策略拦截（{_enum(feedback['blockReason'])}）", "blocked")
    candidates = payload.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates else None
    if not isinstance(candidate, dict):
        raise _SpeechError("接口没有返回音频", "empty", retryable=True)
    finish = candidate.get("finishReason")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    inline: JsonObject | None = None
    for part in parts if isinstance(parts, list) else []:
        data = part.get("inlineData") if isinstance(part, dict) else None
        if isinstance(data, dict) and isinstance(data.get("data"), str) and data["data"]:
            inline = data
            break
    if inline is None:
        if isinstance(finish, str) and finish in _BLOCKED:
            raise _SpeechError(f"内容被安全策略拦截（{finish}）", "blocked")
        raise _SpeechError("接口没有返回音频", "empty", retryable=True)
    encoded = str(inline["data"])
    if len(encoded) > max_bytes * 4 // 3 + 16:
        raise _SpeechError("合成的语音超过媒体大小上限，请缩短文本", "too_large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise _SpeechError("接口返回的音频编码无效", "invalid_audio", retryable=True) from None
    mime_type = inline.get("mimeType")
    wav, duration = normalize_wav(raw, mime_type if isinstance(mime_type, str) else "")
    if len(wav) > max_bytes:
        raise _SpeechError("合成的语音超过媒体大小上限，请缩短文本", "too_large")
    return SpeechAudio(wav, duration, truncated=finish == "MAX_TOKENS")


async def _request(
    client: httpx.AsyncClient,
    tokens: AccessTokens,
    config: TTSSettings,
    account: ServiceAccount,
    text: str,
    style: str | None,
    voice: str | None,
    max_bytes: int,
) -> SpeechAudio:
    token = await tokens.get(client, config.vertex.credentials, account, config.timeout)
    location = config.vertex.location
    project = config.vertex.project_id or account.project_id
    url = (
        f"{ENDPOINTS[location]}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{config.model}:generateContent"
    )
    # 3.8 TTS 把 text 当作逐字稿，风格放在 speechMetadata；采样参数会被接口拒绝，因此不发送。
    part: JsonObject = {"text": text}
    if style:
        part["speechMetadata"] = {"style": style}
    speech: JsonObject = {}
    if voice:
        speech["voiceConfig"] = {"voice": voice}
    if config.language_code:
        speech["languageCode"] = config.language_code
    generation: JsonObject = {"responseModalities": ["AUDIO"]}
    if speech:
        generation["speechConfig"] = speech
    body: JsonObject = {
        "contents": [{"role": "user", "parts": [part]}],
        "generationConfig": generation,
    }
    try:
        async with asyncio.timeout(config.timeout):
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=config.timeout,
            )
    except TimeoutError:
        raise _SpeechError("语音合成请求超时", "timeout", retryable=True) from None
    except httpx.HTTPError as error:
        raise _transport_error(error, "语音合成接口") from None
    if response.is_error:
        status = response.status_code
        details = google_error_metadata(_error_payload(response))
        raise _SpeechError(
            f"语音合成接口返回 HTTP {status}" + (f"；{details}" if details else ""),
            "http",
            retryable=status in (408, 429) or status >= 500,
            retry_after=retry_after_seconds(response.headers.get("retry-after")),
            http_status=status,
            reauthenticate=status == 401,
        )
    if len(response.content) > max_bytes * 4 // 3 + 1_048_576:
        raise _SpeechError("合成的语音超过媒体大小上限，请缩短文本", "too_large")
    try:
        payload = JSON_OBJECT.validate_json(response.content)
    except ValueError:
        raise _SpeechError(
            "语音合成响应不是有效 JSON", "invalid_response", retryable=True
        ) from None
    return _audio(payload, max_bytes)


async def synthesize_speech(
    config: TTSSettings,
    text: str,
    style: str | None,
    voice: str | None,
    *,
    tokens: AccessTokens,
    max_bytes: int,
    client: httpx.AsyncClient | None = None,
    checkpoint: Callable[[], ToolResult | None] = lambda: None,
) -> SpeechAudio | ToolResult:
    """含重试的完整合成；工具与控制台试听共用。每次请求前经 checkpoint 检查取消与授权。"""
    try:
        account = config.vertex.account()
    except ValueError:
        return ToolResult.failure("服务账号凭据无效，请重新导入", "SpeechSynthesisError")
    owned = client is None
    http = client or httpx.AsyncClient(timeout=config.timeout, proxy=config.proxy, trust_env=False)
    try:
        retry = 0
        reauthenticated = False
        while True:
            if stopped := checkpoint():
                return stopped
            try:
                return await _request(http, tokens, config, account, text, style, voice, max_bytes)
            except _SpeechError as error:
                if error.reauthenticate and not reauthenticated:
                    # 令牌可能已被吊销；作废缓存后重新签发一次，不计入重试次数。
                    tokens.invalidate()
                    reauthenticated = True
                    continue
                if not error.retryable or retry >= config.max_retries:
                    logger.warning(
                        "tts_request_failed",
                        extra={
                            "request_kind": "tts",
                            "retry": retry,
                            "max_retries": config.max_retries,
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
                        "request_kind": "tts",
                        "retry": retry,
                        "max_retries": config.max_retries,
                        "delay_seconds": delay,
                        "http_status": error.http_status,
                        "error_type": error.kind,
                    },
                )
                await asyncio.sleep(delay)
    finally:
        if owned:
            await http.aclose()


def build_tools(
    get_settings: Callable[[], TTSSettings],
    napcat_settings: NapCatSettings | None = None,
    *,
    tokens: AccessTokens | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[ToolDefinition]:
    shared = tokens or AccessTokens()
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
        voice = request.voice or config.voice or None
        audio = await synthesize_speech(
            config,
            request.text,
            request.style,
            voice,
            tokens=shared,
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
        }
        if voice:
            data["voice"] = voice
        if audio.truncated:
            data["truncated"] = True
            data["notice"] = "输出达到接口上限，结尾被截断；需要完整内容时请分段合成"
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
                "把文字合成为语音（Gemini TTS）。text 只写要读出的原文，"
                "语气和情绪写在 style，不要写进 text。"
                "返回的 generated_id 用 qq__send_message 的 {type: record, generated_id} 发送。"
            ),
            "tts",
            model_binding(SynthesizeSpeech, synthesize),
            title="合成语音",
        )
    ]
