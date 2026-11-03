"""Gemini TTS：服务账号签发、Vertex 请求格式、重试与脱敏、权限边界、语音发送及控制台试听。"""

import base64
import io
import json
import logging
import wave
from collections import OrderedDict
from collections.abc import Callable
from functools import cache
from pathlib import Path

import httpx
import jwt
import pytest
import tomlkit
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient
from test_napcat import GROUP, Bot

from qq_agent_harness import contracts
from qq_agent_harness.configuration import ConfigError, ConfigStore
from qq_agent_harness.contracts import (
    GeneratedMedia,
    JsonObject,
    JsonValue,
    ToolContext,
    ToolResult,
    remember_generated,
)
from qq_agent_harness.napcat import build_tools as build_qq_tools
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.sandbox import RunnerClient
from qq_agent_harness.sandbox import build_tools as build_sandbox_tools
from qq_agent_harness.settings import (
    ConversationSettings,
    HarnessSettings,
    Policy,
    SandboxSettings,
    TTSSettings,
    VertexTTSSettings,
)
from qq_agent_harness.tts import TOKEN_URL, AccessTokens, design_voice, list_voices
from qq_agent_harness.tts import build_tools as build_tts_tools
from qq_agent_harness.web import create_app

VERTEX_PATH = (
    "/v1/projects/demo-project-123/locations/global"
    "/publishers/google/models/gemini-3.8-flash-tts:generateContent"
)
VOICES_PATH = "/v1beta1/projects/demo-project-123/locations/global/voices"
SECRET_TEXT = "只该出现在请求正文里的台词"


@cache
def service_account(project: str | None = "demo-project-123") -> tuple[str, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    data: JsonObject = {
        "type": "service_account",
        "private_key_id": "kid-1",
        "private_key": key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(
            "ascii"
        ),
        "client_email": "tts@demo-project-123.iam.gserviceaccount.com",
        # 令牌地址固定使用 Google 官方地址，不取自密钥文件。
        "token_uri": "https://token.attacker.example/token",
        "universe_domain": "googleapis.com",
    }
    if project is not None:
        data["project_id"] = project
    return json.dumps(data, indent=2), key.public_key()


def wav_bytes(seconds: float = 0.5, rate: int = 24_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x01\x00" * int(rate * seconds))
    return buffer.getvalue()


def audio_response(content: bytes, mime_type: str = "audio/wav") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64.b64encode(content).decode("ascii"),
                                }
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        },
    )


def token_response(token: str = "ya29.first-token") -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": token, "expires_in": 3599, "token_type": "Bearer"}
    )


def setup_tts(
    client: httpx.AsyncClient, tts: TTSSettings | None = None, bot: Bot | None = None
) -> tuple[ToolRegistry, ToolContext, HarnessSettings, Bot]:
    active = bot or Bot()
    settings = HarnessSettings(
        tts=tts
        or TTSSettings(
            enabled=True,
            model="gemini-3.8-flash-tts",
            voice="Kore",
            language_code="cmn-CN",
            max_retries=2,
            vertex=VertexTTSSettings(credentials=service_account()[0]),
        ),
        sandbox=SandboxSettings(enabled=True),
    )
    tools = [
        *build_qq_tools(),
        *build_sandbox_tools(lambda: settings.sandbox, RunnerClient(lambda: settings.sandbox)),
        *build_tts_tools(lambda: settings.tts, client=client),
    ]
    policy = Policy(categories={tool.category: True for tool in tools}, max_sends_per_turn=5)
    settings.groups["10"] = ConversationSettings(enabled=True, policy=policy)
    registry = ToolRegistry(lambda: settings)
    for tool in tools:
        registry.add(tool)
    return registry, ToolContext(GROUP, "test-turn", active), settings, active


def write_config(directory: Path, tts: JsonObject) -> Path:
    (directory / "persona.md").write_text("测试人格", encoding="utf-8")
    path = directory / "harness.toml"
    path.write_text("# 保留这条注释\n" + tomlkit.dumps({"tts": tts}), encoding="utf-8")
    return path


async def test_signed_assertion_request_format_cached_token_and_record_send() -> None:
    credentials, public_key = service_account()
    token_requests: list[httpx.Request] = []
    vertex_requests: list[httpx.Request] = []
    audio = wav_bytes(0.5)

    def serve(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            token_requests.append(request)
            return token_response()
        assert request.url.host == "aiplatform.googleapis.com"
        assert request.url.path == VERTEX_PATH
        vertex_requests.append(request)
        return audio_response(audio)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, bot = setup_tts(client)
        first = await registry.execute(
            context,
            "tts__synthesize",
            {"text": "你好呀 <short pause> 晒太阳吗", "style": "慵懒、俏皮"},
        )
        second = await registry.execute(context, "tts__synthesize", {"text": "第二句"})

    assert first.status == "ok" and isinstance(first.data, dict)
    assert second.status == "ok" and isinstance(second.data, dict)
    # 令牌按服务账号缓存，第二次合成不再签发。
    assert len(token_requests) == 1
    form = httpx.QueryParams(token_requests[0].content.decode())
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assertion = form["assertion"]
    assert jwt.get_unverified_header(assertion)["kid"] == "kid-1"
    claims = jwt.decode(assertion, public_key, algorithms=["RS256"], audience=TOKEN_URL)
    assert claims["iss"] == "tts@demo-project-123.iam.gserviceaccount.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/cloud-platform"
    assert claims["exp"] - claims["iat"] == 3600

    assert [request.headers["authorization"] for request in vertex_requests] == [
        "Bearer ya29.first-token"
    ] * 2
    # 风格放在 speechMetadata，不发送会被 3.8 TTS 拒绝的采样参数；音色固定取自配置。
    assert json.loads(vertex_requests[0].content) == {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": "你好呀 <short pause> 晒太阳吗",
                        "speechMetadata": {"style": "慵懒、俏皮"},
                    }
                ],
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"voice": "Kore"}, "languageCode": "cmn-CN"},
        },
    }
    assert json.loads(vertex_requests[1].content)["generationConfig"]["speechConfig"] == {
        "voiceConfig": {"voice": "Kore"},
        "languageCode": "cmn-CN",
    }

    generated_id = str(first.data["generated_id"])
    assert generated_id.startswith("tts_")
    assert first.data["mime_type"] == "audio/wav"
    assert first.data["duration_seconds"] == 0.5
    assert "voice" not in first.data
    assert context.generated_media[generated_id].content == audio

    sent = await registry.execute(
        context,
        "qq__send_message",
        {
            "parts": [
                {"type": "text", "text": "听听这个"},
                {"type": "record", "generated_id": generated_id},
                {"type": "text", "text": "怎么样"},
            ]
        },
    )
    assert sent.status == "ok"
    # 语音单独成条，前后的文字各自发送并计入发送次数。
    voice_message = [
        {"type": "record", "data": {"file": "base64://" + base64.b64encode(audio).decode()}}
    ]
    assert [call[1]["message"] for call in bot.calls] == [
        [{"type": "text", "data": {"text": "听听这个"}}],
        voice_message,
        [{"type": "text", "data": {"text": "怎么样"}}],
    ]
    assert context.send_attempts == 3


async def test_raw_pcm_is_wrapped_and_project_override_routes_to_multi_region() -> None:
    requests: list[httpx.Request] = []
    pcm = b"\x02\x00" * 24_000

    def serve(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return token_response()
        requests.append(request)
        return audio_response(pcm, "audio/L16;codec=pcm;rate=24000")

    config = TTSSettings(
        enabled=True,
        model="gemini-3.8-flash-lite-tts",
        voice="Charon",
        vertex=VertexTTSSettings(
            credentials=service_account()[0], project_id="other-project-77", location="us"
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, _ = setup_tts(client, config)
        result = await registry.execute(context, "tts__synthesize", {"text": "一秒钟"})

    assert result.status == "ok" and isinstance(result.data, dict)
    assert requests[0].url.host == "aiplatform.us.rep.googleapis.com"
    assert requests[0].url.path == (
        "/v1/projects/other-project-77/locations/us"
        "/publishers/google/models/gemini-3.8-flash-lite-tts:generateContent"
    )
    # 未配置语言时只发送音色，由模型识别语言。
    assert json.loads(requests[0].content)["generationConfig"]["speechConfig"] == {
        "voiceConfig": {"voice": "Charon"}
    }
    content = context.generated_media[str(result.data["generated_id"])].content
    with wave.open(io.BytesIO(content), "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (
            1,
            2,
            24_000,
        )
        assert reader.readframes(reader.getnframes()) == pcm
    assert result.data["duration_seconds"] == 1.0


async def test_revoked_token_is_reissued_once_and_rate_limit_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def fast_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("qq_agent_harness.tts.asyncio.sleep", fast_sleep)
    tokens = iter(["ya29.revoked", "ya29.fresh"])
    seen: list[str] = []
    responses = iter(
        [
            httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}}),
            httpx.Response(
                429,
                headers={"retry-after": "2"},
                json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}},
            ),
            audio_response(wav_bytes(0.25)),
        ]
    )

    def serve(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return token_response(next(tokens))
        seen.append(request.headers["authorization"])
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, _ = setup_tts(client)
        result = await registry.execute(context, "tts__synthesize", {"text": "重试"})

    assert result.status == "ok"
    # 401 只重新签发一次，不计入重试；429 按 Retry-After 等待后重试。
    assert seen == ["Bearer ya29.revoked", "Bearer ya29.fresh", "Bearer ya29.fresh"]
    assert delays == [2.0]


@pytest.mark.parametrize(
    ("scenario", "vertex_calls", "expected"),
    [
        ("service_disabled", 1, "SERVICE_DISABLED：项目未启用 Vertex AI API"),
        ("invalid_grant", 0, "invalid_grant：服务账号密钥无效或已删除"),
        ("blocked", 1, "内容被安全策略拦截（PROHIBITED_CONTENT）"),
        ("empty", 3, "接口没有返回音频；已重试 2 次"),
    ],
)
async def test_failures_are_classified_and_never_leak_secrets(
    scenario: str,
    vertex_calls: int,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("qq_agent_harness.tts.asyncio.sleep", fast_sleep)
    calls = 0

    def serve(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if str(request.url) == TOKEN_URL:
            if scenario == "invalid_grant":
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "Invalid JWT: " + SECRET_TEXT,
                    },
                )
            return token_response("ya29.secret-token")
        calls += 1
        if scenario == "service_disabled":
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": 403,
                        "status": "PERMISSION_DENIED",
                        "message": "API disabled for " + SECRET_TEXT,
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                "reason": "SERVICE_DISABLED",
                            }
                        ],
                    }
                },
            )
        if scenario == "blocked":
            return httpx.Response(
                200, json={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
            )
        return httpx.Response(200, json={"candidates": [{"finishReason": "OTHER"}]})

    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, _ = setup_tts(client)
        result = await registry.execute(context, "tts__synthesize", {"text": SECRET_TEXT})

    assert result.status == "error" and result.error_type == "SpeechSynthesisError"
    assert expected in result.message
    assert calls == vertex_calls
    assert not context.generated_media
    private_key = json.loads(service_account()[0])["private_key"]
    logged = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    for secret in (SECRET_TEXT, "ya29.secret-token", private_key[40:80]):
        assert secret not in result.message
        assert secret not in logged


async def test_tool_is_gated_by_config_and_checks_workspace_and_cancellation_first() -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == TOKEN_URL:
            return token_response()
        return audio_response(wav_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, settings, _ = setup_tts(client)
        tool = registry.tools["tts__synthesize"]
        assert "tts__synthesize" in registry.available(GROUP)
        settings.tts.model = ""
        assert registry.permitted(GROUP, tool) == (False, "语音合成尚未选择模型")
        settings.tts.model = "gemini-3.8-flash-tts"
        settings.tts.vertex.credentials = ""
        assert registry.permitted(GROUP, tool) == (False, "语音合成尚未导入服务账号")
        settings.tts.vertex.credentials = service_account()[0]
        # 音色由管理员固定；没有选定音色时工具不可用，不退回接口默认音色。
        settings.tts.voice = ""
        assert registry.permitted(GROUP, tool) == (False, "语音合成尚未选择音色")
        settings.tts.voice = "Kore"
        settings.tts.enabled = False
        denied = await registry.execute(context, "tts__synthesize", {"text": "未启用"})
        assert denied.error_type == "PermissionDenied"
        settings.tts.enabled = True
        settings.groups["10"].policy.categories["tts"] = False
        assert "tts__synthesize" not in registry.available(GROUP)
        settings.groups["10"].policy.categories["tts"] = True

        settings.groups["10"].policy.tools["sandbox__write_file"] = False
        denied = await registry.execute(
            context, "tts__synthesize", {"text": "存一份", "workspace_path": "voice/a.wav"}
        )
        assert denied.error_type == "PermissionDenied"

        context.cancelled = lambda: True
        stopped = await registry.execute(context, "tts__synthesize", {"text": "已停止"})
        assert stopped.status == "cancelled"
        assert not requests

        context.cancelled = lambda: False
        settings.groups["10"].policy.tools["sandbox__write_file"] = True
        written: dict[str, bytes] = {}

        async def write(path: str, content: bytes) -> None:
            written[path] = content

        context.write_workspace = write
        saved = await registry.execute(
            context, "tts__synthesize", {"text": "存一份", "workspace_path": "voice/a.wav"}
        )
    assert saved.status == "ok" and isinstance(saved.data, dict)
    assert saved.data["workspace_path"] == "voice/a.wav"
    assert written == {"voice/a.wav": wav_bytes()}


async def test_agent_cannot_choose_the_voice() -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return token_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, _ = setup_tts(client)
        definition = next(
            item for item in registry.definitions(context) if item["name"] == "tts__synthesize"
        )
        parameters = definition["parameters"]
        assert isinstance(parameters, dict) and isinstance(parameters["properties"], dict)
        assert set(parameters["properties"]) == {"text", "style", "workspace_path"}
        result = await registry.execute(
            context, "tts__synthesize", {"text": "换个声音", "voice": "Puck"}
        )
    assert result.status == "error" and result.error_type == "InvalidArguments"
    assert not requests


def test_custom_voice_requires_global_location(tmp_path: Path) -> None:
    store = ConfigStore(write_config(tmp_path, {"enabled": False}))
    config = store.snapshot()["config"]
    assert isinstance(config, dict) and isinstance(config["tts"], dict)
    config["tts"]["voice"] = "voice_abc123"
    config["tts"]["vertex"] = {"credentials": service_account()[0], "location": "us"}
    with pytest.raises(ConfigError) as caught:
        store.save(config, store.last_sha)
    assert caught.value.issues == [
        {"path": ["tts", "vertex", "location"], "message": "自定义音色只能在全球区域使用"}
    ]

    # 内置音色和音色库音色不受区域限制；自定义音色改用全球区域后可以保存。
    config["tts"]["voice"] = "zh-cn-concierge-1"
    store.save(config, store.last_sha)
    config["tts"]["voice"] = "voice_abc123"
    config["tts"]["vertex"]["location"] = "global"
    store.save(config, store.last_sha)
    assert ConfigStore(store.path).settings.tts.voice == "voice_abc123"


def test_console_designs_lists_auditions_and_deletes_voices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcm = b"\x03\x00" * 12_000
    sample = {
        "mime_type": "audio/l16; rate=24000; channels=1",
        "data": base64.b64encode(pcm).decode("ascii"),
    }
    seen: list[tuple[str, str, str]] = []
    designs: list[JsonValue] = []

    def serve(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return token_response()
        # 音色接口固定走全球区域的 v1beta1。
        assert request.url.host == "aiplatform.googleapis.com"
        assert request.url.path.startswith(VOICES_PATH)
        seen.append((request.method, request.url.path, request.url.query.decode()))
        if request.method == "POST":
            designs.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "voice_new01", "sample_audio": sample})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/voice_new01"):
            return httpx.Response(200, json={"id": "voice_new01", "sample_audio": sample})
        if "page_token" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {"id": "voice_a1", "type": "VOICE_TYPE_PROMPTED", "display_name": " 小夏 "},
                        # 不符合格式的 ID 会拼进请求路径，必须忽略。
                        {"id": "../voice_x", "type": "VOICE_TYPE_PROMPTED"},
                        {"id": "Kore", "type": "VOICE_TYPE_PREBUILT"},
                    ],
                    "next_page_token": "p2",
                },
            )
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"id": "voice_b2", "type": "VOICE_TYPE_REPLICATED", "displayName": "复刻"}
                ]
            },
        )

    real_client: Callable[..., httpx.AsyncClient] = httpx.AsyncClient
    monkeypatch.setattr(
        "qq_agent_harness.tts.httpx.AsyncClient",
        lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(serve)}),
    )
    config = write_config(
        tmp_path,
        {
            "model": "gemini-3.8-flash-tts",
            "voice": "voice_a1",
            "vertex": {"credentials": service_account()[0]},
        },
    )
    with TestClient(create_app(config, connect_mcp=False)) as client:
        listed = client.get("/api/tts/voices")
        assert listed.status_code == 200
        assert listed.json() == {
            "voices": [
                {"id": "voice_a1", "name": "小夏", "kind": "prompted"},
                {"id": "voice_b2", "name": "复刻", "kind": "replicated"},
            ]
        }
        assert seen == [
            ("GET", VOICES_PATH, "type=prompted&type=replicated"),
            ("GET", VOICES_PATH, "type=prompted&type=replicated&page_token=p2"),
        ]

        designed = client.post(
            "/api/tts/voice-design",
            json={"name": " 小冬 ", "description": "二十多岁的女声，清亮柔和，语速适中。"},
        )
        assert designed.status_code == 200
        body = designed.json()
        assert (body["id"], body["name"]) == ("voice_new01", "小冬")
        assert designs == [
            {
                "store": True,
                "voice": {
                    "type": "VOICE_TYPE_PROMPTED",
                    "displayName": "小冬",
                    "prompted": {"input": "二十多岁的女声，清亮柔和，语速适中。"},
                },
            }
        ]
        # 接口给出的裸 PCM 样音补成 WAV 后返回。
        with wave.open(io.BytesIO(base64.b64decode(body["sample"])), "rb") as reader:
            assert (reader.getframerate(), reader.readframes(reader.getnframes())) == (24_000, pcm)

        audition = client.get("/api/tts/voices/voice_new01/sample")
        assert audition.status_code == 200 and audition.headers["content-type"] == "audio/wav"

        seen.clear()
        current = client.delete("/api/tts/voices/voice_a1")
        assert current.status_code == 409 and "正在使用" in current.json()["detail"]
        invalid = client.delete("/api/tts/voices/bad.id")
        assert invalid.status_code == 400
        assert not seen
        deleted = client.delete("/api/tts/voices/voice_b2")
        assert deleted.status_code == 200
        assert seen == [("DELETE", VOICES_PATH + "/voice_b2", "")]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("timeout", "音色接口连接失败（ReadTimeout）"),
        ("unavailable", "音色接口返回 HTTP 503；UNAVAILABLE：服务暂时不可用"),
        ("quota", "RESOURCE_EXHAUSTED / RATE_LIMIT_EXCEEDED：项目的音色创建配额不足"),
    ],
)
async def test_voice_design_is_never_retried_and_errors_stay_sanitized(
    failure: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("qq_agent_harness.tts.asyncio.sleep", fast_sleep)
    posts = 0

    def serve(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if str(request.url) == TOKEN_URL:
            return token_response()
        posts += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("slow " + SECRET_TEXT, request=request)
        if failure == "unavailable":
            return httpx.Response(
                503, json={"error": {"status": "UNAVAILABLE", "message": SECRET_TEXT}}
            )
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Quota exceeded for " + SECRET_TEXT,
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "RATE_LIMIT_EXCEEDED",
                            "metadata": {
                                "quota_metric": "aiplatform.googleapis.com/voice_creations",
                                "consumer": "projects/" + SECRET_TEXT,
                            },
                        }
                    ],
                }
            },
        )

    caplog.set_level(logging.INFO)
    config = TTSSettings(max_retries=3, vertex=VertexTTSSettings(credentials=service_account()[0]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        result = await design_voice(config, "小夏", "女声", tokens=AccessTokens(), client=client)

    # 创建按次计费，超时或服务端错误后可能已经创建，重试会重复创建。
    assert posts == 1
    assert isinstance(result, ToolResult) and result.status == "error"
    assert expected in result.message
    logged = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert SECRET_TEXT not in result.message and SECRET_TEXT not in logged


async def test_voice_listing_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("qq_agent_harness.tts.asyncio.sleep", fast_sleep)
    responses = iter(
        [
            httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}),
            # 项目里还没有自定义音色时，接口返回空对象。
            httpx.Response(200, json={}),
        ]
    )

    def serve(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return token_response()
        return next(responses)

    config = TTSSettings(max_retries=1, vertex=VertexTTSSettings(credentials=service_account()[0]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        assert await list_voices(config, tokens=AccessTokens(), client=client) == []


async def test_generated_media_types_are_checked_before_any_send() -> None:
    async with httpx.AsyncClient() as client:
        registry, context, _, bot = setup_tts(client)
        context.generated_media["tts_voice"] = GeneratedMedia("tts_voice", wav_bytes(), "audio/wav")
        context.generated_media["gen_image"] = GeneratedMedia("gen_image", b"\x89PNG", "image/png")
        attempts: list[tuple[str, JsonObject]] = [
            ("qq__send_message", {"parts": [{"type": "record", "generated_id": "gen_image"}]}),
            ("qq__send_message", {"parts": [{"type": "image", "generated_id": "tts_voice"}]}),
            (
                "qq__send_message",
                {
                    "parts": [
                        {"type": "reply", "message_id": "30"},
                        {"type": "record", "generated_id": "tts_voice"},
                    ]
                },
            ),
            ("qq__set_group_portrait", {"generated_id": "tts_voice"}),
        ]
        for name, arguments in attempts:
            result = await registry.execute(context, name, arguments)
            assert result.status == "error", (name, arguments)
        assert not [action for action, _ in bot.calls if action.startswith(("send_", "set_"))]
        assert context.send_attempts == 0


def test_generated_media_is_bounded_by_count_and_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    store: OrderedDict[str, GeneratedMedia] = OrderedDict()
    for index in range(contracts.MAX_GENERATED_MEDIA + 1):
        remember_generated(store, GeneratedMedia(f"m{index}", b"x", "audio/wav"))
    assert list(store)[0] == "m1" and len(store) == contracts.MAX_GENERATED_MEDIA

    monkeypatch.setattr(contracts, "MAX_GENERATED_BYTES", 100)
    store.clear()
    for name in ("a", "b", "c"):
        remember_generated(store, GeneratedMedia(name, b"x" * 40, "audio/wav"))
    assert list(store) == ["b", "c"]
    # 单个生成物超过总量时仍保留最新一项，交给发送时的媒体上限判断。
    remember_generated(store, GeneratedMedia("big", b"x" * 300, "audio/wav"))
    assert list(store) == ["big"]


def test_invalid_credentials_are_rejected_on_save_without_echo(tmp_path: Path) -> None:
    credentials, _ = service_account()
    store = ConfigStore(write_config(tmp_path, {"enabled": False}))
    data = json.loads(credentials)
    private_key = data["private_key"]
    cases: list[tuple[str, list[str], str]] = [
        ("{" + private_key, ["tts", "vertex", "credentials"], "不是有效的 JSON"),
        (
            json.dumps({**data, "type": "authorized_user"}),
            ["tts", "vertex", "credentials"],
            "service_account",
        ),
        (
            json.dumps({**data, "private_key": private_key.replace("MII", "XYZ")}),
            ["tts", "vertex", "credentials"],
            "私钥无法解析",
        ),
        (
            json.dumps({**data, "universe_domain": "example.com"}),
            ["tts", "vertex", "credentials"],
            "googleapis.com",
        ),
        (service_account(None)[0], ["tts", "vertex", "project_id"], "没有 project_id"),
    ]
    for value, path, fragment in cases:
        config = store.snapshot()["config"]
        assert isinstance(config, dict) and isinstance(config["tts"], dict)
        config["tts"]["vertex"] = {"credentials": value}
        with pytest.raises(ConfigError) as caught:
            store.save(config, store.last_sha)
        issue = caught.value.issues[0]
        assert issue["path"] == path and fragment in str(issue["message"])
        assert private_key[40:80] not in json.dumps(caught.value.issues, ensure_ascii=False)

    config = store.snapshot()["config"]
    assert isinstance(config, dict) and isinstance(config["tts"], dict)
    config["tts"]["vertex"] = {"credentials": credentials, "location": "eu"}
    store.save(config, store.last_sha)
    text = store.path.read_text(encoding="utf-8")
    assert text.startswith("# 保留这条注释")
    reloaded = ConfigStore(store.path).settings.tts.vertex
    assert reloaded.credentials == credentials and reloaded.location == "eu"
    assert reloaded.account().project_id == "demo-project-123"


def test_console_preview_returns_wav_and_sanitized_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = wav_bytes(0.25)
    mode = {"vertex": "ok"}

    def serve(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return token_response()
        if mode["vertex"] == "denied":
            return httpx.Response(
                403,
                json={
                    "error": {
                        "status": "PERMISSION_DENIED",
                        "message": SECRET_TEXT,
                        "details": [{"reason": "BILLING_DISABLED"}],
                    }
                },
            )
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][0] == {
            "text": "试听",
            "speechMetadata": {"style": "开心"},
        }
        assert body["generationConfig"]["speechConfig"]["voiceConfig"] == {"voice": "Leda"}
        return audio_response(audio)

    real_client: Callable[..., httpx.AsyncClient] = httpx.AsyncClient
    monkeypatch.setattr(
        "qq_agent_harness.tts.httpx.AsyncClient",
        lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(serve)}),
    )
    config = write_config(
        tmp_path,
        {
            "model": "gemini-3.8-flash-tts",
            "voice": "Kore",
            "vertex": {"credentials": service_account()[0]},
        },
    )
    with TestClient(create_app(config, connect_mcp=False)) as client:
        catalog = client.get("/api/tools").json()
        assert {"id": "tts", "label": "语音合成"} in catalog["categories"]
        assert any(item["name"] == "tts__synthesize" for item in catalog["tools"])

        # 未启用语音合成时，控制台仍可先试听确认凭据、模型和音色。
        ok = client.post(
            "/api/tts/preview", json={"text": "试听", "style": "开心", "voice": "Leda"}
        )
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "audio/wav"
        assert ok.content == audio

        mode["vertex"] = "denied"
        denied = client.post("/api/tts/preview", json={"text": "试听", "voice": "Leda"})
        assert denied.status_code == 502
        assert "BILLING_DISABLED" in denied.json()["detail"]
        assert SECRET_TEXT not in denied.text

        too_long = client.post("/api/tts/preview", json={"text": "长" * 201})
        assert too_long.status_code == 422

    empty = write_config(tmp_path, {"model": "gemini-3.8-flash-tts"})
    with TestClient(create_app(empty, connect_mcp=False)) as client:
        missing = client.post("/api/tts/preview", json={"text": "试听"})
        assert missing.status_code == 400 and missing.json()["detail"] == "请先导入服务账号"

    # 没有选定音色时不退回接口默认音色，试听也要先选音色。
    silent = write_config(
        tmp_path,
        {"model": "gemini-3.8-flash-tts", "vertex": {"credentials": service_account()[0]}},
    )
    with TestClient(create_app(silent, connect_mcp=False)) as client:
        missing = client.post("/api/tts/preview", json={"text": "试听"})
        assert missing.status_code == 400 and missing.json()["detail"] == "请先选择音色"
