"""本机 IndexTTS 2.5：4种情绪控制模式、远端状态与显存管理、音色库管理、重试与脱敏、权限边界及本机服务端联调。"""

import base64
import io
import json
import logging
import shutil
import struct
import sys
import wave
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import tomlkit
from fastapi.testclient import TestClient
from test_napcat import GROUP, Bot

from deploy.indextts.server import IndexTTSEngine, create_indextts_app
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
)
from qq_agent_harness.tts import build_tools as build_tts_tools
from qq_agent_harness.tts import list_voices, upload_voice
from qq_agent_harness.web import create_app

SECRET_TEXT = "只该出现在请求正文里的敏感台词"


def test_engine_uses_indextts25_constructor_and_required_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """外部模型替身保持 2.5 的必要参数，覆盖原先跳过真实适配层的测试空缺。"""
    (tmp_path / "config.yaml").write_text("version: 2.5", encoding="utf-8")
    calls: list[tuple[str, object]] = []
    audio = wav_bytes()

    class UpstreamModel:
        def __init__(
            self,
            *,
            cfg_path: str,
            model_dir: str,
            use_bf16: bool,
            use_cuda_kernel: bool,
            use_qwen_emo: bool,
        ) -> None:
            assert Path(cfg_path).is_file() and Path(model_dir).is_dir()
            if not use_qwen_emo:
                raise RuntimeError("QwenEmotion 未加载")

        def infer(
            self,
            spk_audio_prompt: str,
            text: str,
            output_path: str,
            lang: str,
            *,
            verbose: bool,
            use_emo_text: bool = False,
            emo_text: str | None = None,
            emo_vector: list[float] | None = None,
            emo_audio_prompt: str | None = None,
            emo_alpha: float = 1.0,
        ) -> None:
            assert Path(spk_audio_prompt).is_file()
            calls.append((lang, emo_text if use_emo_text else emo_vector or emo_audio_prompt))
            print(SECRET_TEXT)
            Path(output_path).write_bytes(audio)

    module = ModuleType("indextts.infer_v2_5")
    monkeypatch.setattr(module, "IndexTTS2", UpstreamModel, raising=False)
    monkeypatch.setitem(sys.modules, "indextts", ModuleType("indextts"))
    monkeypatch.setitem(sys.modules, "indextts.infer_v2_5", module)
    engine = IndexTTSEngine(tmp_path)
    app = create_indextts_app(tmp_path / "voices", tmp_path, engine=engine)
    with TestClient(app) as client:
        uploaded = client.post(
            "/v1/voices",
            json={
                "name": "参考",
                "id": "reference",
                "audio": base64.b64encode(audio).decode(),
            },
        )
        assert uploaded.status_code == 200
        modes = [
            {},
            {"emotion_mode": "text", "emotion_text": "开心"},
            {"emotion_mode": "vector", "emotion_vector": [1.0, 0, 0, 0, 0, 0, 0, 0]},
            {"emotion_mode": "audio", "emotion_voice": "reference"},
        ]
        for mode in modes:
            response = client.post(
                "/v1/synthesize",
                json={
                    "voice": "reference",
                    "text": "Hello",
                    "language": "en",
                    **mode,
                },
            )
            assert response.status_code == 200 and response.content == audio
    assert [lang for lang, _ in calls] == ["en"] * 4
    assert calls[0][1] is None and calls[1][1] == "开心"
    assert calls[2][1] == [1.0, 0, 0, 0, 0, 0, 0, 0]
    assert calls[3][1] == str(tmp_path / "voices" / "reference.wav")
    assert SECRET_TEXT not in capsys.readouterr().out


def test_local_service_rejects_invalid_emotion_without_echoing_input(tmp_path: Path) -> None:
    app = create_indextts_app(tmp_path / "voices", tmp_path, api_key="local-key")
    with TestClient(app) as client:
        assert client.get("/v1/voices").status_code == 401
        for invalid in [
            {"emotion_mode": "vector", "emotion_vector": [0] * 8},
            {"emotion_mode": "vector", "emotion_vector": [2] * 8},
            {"emotion_mode": "text"},
            {"emotion_mode": "reference", "emotion_text": SECRET_TEXT},
        ]:
            response = client.post(
                "/v1/synthesize",
                headers={"Authorization": "Bearer local-key"},
                json={
                    "voice": "reference",
                    "text": SECRET_TEXT,
                    **invalid,
                },
            )
            assert response.status_code == 422
            assert SECRET_TEXT not in response.text


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要实际 ffmpeg 音频转换")
def test_local_service_converts_float_wav_reference(tmp_path: Path) -> None:
    # 官方示例采用 IEEE float WAV；wave 模块只能读取 PCM，必须进入转换流程。
    frames = struct.pack("<f", 0.25) * 2400
    floating_wav = (
        b"RIFF"
        + struct.pack("<I", 36 + len(frames))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 3, 1, 24000, 96000, 4, 32)
        + b"data"
        + struct.pack("<I", len(frames))
        + frames
    )
    with TestClient(create_indextts_app(tmp_path / "voices", tmp_path)) as client:
        uploaded = client.post(
            "/v1/voices",
            json={
                "id": "float_sample",
                "name": "浮点参考",
                "audio": base64.b64encode(floating_wav).decode(),
            },
        )
        assert uploaded.status_code == 200
        response = client.get("/v1/voices/float_sample/sample")
        assert response.status_code == 200
        with wave.open(io.BytesIO(response.content), "rb") as reader:
            assert reader.getsampwidth() == 2
            assert reader.getnframes() / reader.getframerate() == pytest.approx(0.1)


def wav_bytes(seconds: float = 0.5, rate: int = 24_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x01\x00" * int(rate * seconds))
    return buffer.getvalue()


def audio_response(content: bytes, mime_type: str = "audio/wav") -> httpx.Response:
    return httpx.Response(200, content=content, headers={"Content-Type": mime_type})


def setup_tts(
    client: httpx.AsyncClient, tts: TTSSettings | None = None, bot: Bot | None = None
) -> tuple[ToolRegistry, ToolContext, HarnessSettings, Bot]:
    active = bot or Bot()
    settings = HarnessSettings(
        tts=tts
        or TTSSettings(
            enabled=True,
            base_url="http://192.168.31.100:7861",
            api_key="indextts-secret-key",
            voice="elaina_calm",
            max_retries=2,
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


async def test_agent_chooses_emotion_modes_and_sends_record() -> None:
    requests: list[httpx.Request] = []
    audio = wav_bytes(0.5)

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == "http://192.168.31.100:7861/v1/synthesize"
        assert request.headers["authorization"] == "Bearer indextts-secret-key"
        return audio_response(audio)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, bot = setup_tts(client)

        # 1. reference 模式（默认跟随原声）
        r1 = await registry.execute(context, "tts__synthesize", {"text": "第一句原声"})
        # 2. text 模式（传入 emotion_text 自动推断或显式指定）
        r2 = await registry.execute(
            context,
            "tts__synthesize",
            {"text": "第二句傲娇", "emotion_text": "傲娇、嘴硬心软", "emotion_alpha": 1.2},
        )
        # 3. vector 模式（8 维情感向量配比）
        r3 = await registry.execute(
            context,
            "tts__synthesize",
            {
                "text": "第三句委屈生气",
                "emotion_mode": "vector",
                "emotion_vector": {"angry": 0.6, "sad": 0.4},
                "emotion_alpha": 0.9,
            },
        )
        # 4. audio 模式（借用独立情感参考音频，说话音色保持为配置中的 elaina_calm）
        r4 = await registry.execute(
            context,
            "tts__synthesize",
            {
                "text": "第四句借用哭腔",
                "emotion_voice": "crying_ref",
                "emotion_alpha": 1.1,
            },
        )

        assert [r.status for r in (r1, r2, r3, r4)] == ["ok", "ok", "ok", "ok"]
        assert isinstance(r1.data, dict) and r1.data["emotion_mode"] == "reference"
        assert isinstance(r2.data, dict) and r2.data["emotion_mode"] == "text"
        assert isinstance(r3.data, dict) and r3.data["emotion_mode"] == "vector"
        assert isinstance(r4.data, dict) and r4.data["emotion_mode"] == "audio"

        assert [json.loads(req.content) for req in requests] == [
            {
                "voice": "elaina_calm",
                "text": "第一句原声",
                "language": "zh",
                "emotion_mode": "reference",
                "emotion_alpha": 1.0,
            },
            {
                "voice": "elaina_calm",
                "text": "第二句傲娇",
                "language": "zh",
                "emotion_mode": "text",
                "emotion_text": "傲娇、嘴硬心软",
                "emotion_alpha": 1.2,
            },
            {
                "voice": "elaina_calm",
                "text": "第三句委屈生气",
                "language": "zh",
                "emotion_mode": "vector",
                "emotion_vector": [0.0, 0.6, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0],
                "emotion_alpha": 0.9,
            },
            {
                "voice": "elaina_calm",
                "text": "第四句借用哭腔",
                "language": "zh",
                "emotion_mode": "audio",
                "emotion_voice": "crying_ref",
                "emotion_alpha": 1.1,
            },
        ]

        generated_id = str(r1.data["generated_id"])
        assert generated_id.startswith("tts_")
        assert r1.data == {
            "generated_id": generated_id,
            "mime_type": "audio/wav",
            "size": len(audio),
            "duration_seconds": 0.5,
            "emotion_mode": "reference",
        }
        sent = await registry.execute(
            context,
            "qq__send_message",
            {"parts": [{"type": "record", "generated_id": generated_id}]},
        )

    assert sent.status == "ok"
    expected_b64 = "base64://" + base64.b64encode(audio).decode("ascii")
    assert bot.calls == [
        (
            "send_group_msg",
            {
                "group_id": "10",
                "message": [
                    {
                        "data": {"file": expected_b64},
                        "type": "record",
                    }
                ],
            },
        )
    ]


async def test_emotion_validation_rejects_conflicting_or_incomplete_arguments() -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return audio_response(wav_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, _ = setup_tts(client)
        invalid_cases: list[JsonObject] = [
            # 试图更换固定说话音色
            {"text": "换个声音", "voice": "other_voice"},
            # 同时传两种情绪载荷
            {"text": "冲突", "emotion_text": "开心", "emotion_voice": "ref_a"},
            # reference 模式却附带 emotion_text
            {"text": "冲突", "emotion_mode": "reference", "emotion_text": "开心"},
            # text 模式缺少 emotion_text
            {"text": "缺失", "emotion_mode": "text"},
            # vector 模式缺少 emotion_vector
            {"text": "缺失", "emotion_mode": "vector"},
            # vector 模式全为 0
            {"text": "全零", "emotion_vector": {"happy": 0.0, "angry": 0.0}},
            # audio 模式缺少 emotion_voice
            {"text": "缺失", "emotion_mode": "audio"},
            # 非法 emotion_voice ID
            {"text": "非法路径", "emotion_voice": "../escape"},
        ]
        for payload in invalid_cases:
            result = await registry.execute(context, "tts__synthesize", payload)
            assert result.status == "error" and result.error_type == "InvalidArguments", payload
    assert not requests


async def test_pcm_and_json_base64_responses_are_normalized_and_bounded() -> None:
    pcm = b"\x02\x00" * 24_000
    call_idx = 0

    def serve(_request: httpx.Request) -> httpx.Response:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return httpx.Response(
                200,
                content=pcm,
                headers={"Content-Type": "audio/L16; rate=24000; channels=1"},
            )
        return httpx.Response(
            200,
            json={
                "mime_type": "audio/L16; rate=24000; channels=1",
                "audio": base64.b64encode(pcm).decode("ascii"),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, settings, _ = setup_tts(client)
        first = await registry.execute(context, "tts__synthesize", {"text": "裸 PCM"})
        second = await registry.execute(context, "tts__synthesize", {"text": "JSON 封装"})
        assert first.status == "ok" and isinstance(first.data, dict)
        assert second.status == "ok" and isinstance(second.data, dict)
        gen_id = str(first.data["generated_id"])
        stored = context.generated_media[gen_id]
        with wave.open(io.BytesIO(stored.content), "rb") as reader:
            assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (
                1,
                2,
                24_000,
            )
            assert reader.readframes(reader.getnframes()) == pcm

        # 超过 media_max_bytes 时拒绝
        small_tools = build_tts_tools(
            lambda: settings.tts,
            settings.napcat.model_copy(update={"media_max_bytes": 1024}),
            client=client,
        )
        small_registry = ToolRegistry(lambda: settings)
        for tool in small_tools:
            small_registry.add(tool)
        oversized = await small_registry.execute(context, "tts__synthesize", {"text": "太大了"})
        assert oversized.status == "error" and "超过媒体大小上限" in oversized.message


async def test_retries_transient_errors_and_sanitizes_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("qq_agent_harness.tts.asyncio.sleep", record_sleep)
    responses = iter(
        [
            httpx.Response(
                429,
                headers={"Retry-After": "3"},
                json={"error": "model_busy", "message": SECRET_TEXT},
            ),
            httpx.Response(503, json={"error": "model_not_loaded", "message": SECRET_TEXT}),
            audio_response(wav_bytes(0.25)),
        ]
    )

    def serve(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, _, _ = setup_tts(client)
        result = await registry.execute(context, "tts__synthesize", {"text": "重试成功"})
    assert result.status == "ok"
    assert delays == [3.0, 2.0]

    # 永久错误（如音色不存在、鉴权失败）不重试，且错误提示与日志不回显敏感原文。
    permanent_calls = 0

    def serve_permanent(_request: httpx.Request) -> httpx.Response:
        nonlocal permanent_calls
        permanent_calls += 1
        return httpx.Response(
            404,
            json={"error": "voice_not_found", "message": f"泄露内容：{SECRET_TEXT}"},
        )

    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve_permanent)) as client:
        registry, context, _, _ = setup_tts(client)
        failed = await registry.execute(context, "tts__synthesize", {"text": SECRET_TEXT})
    assert permanent_calls == 1
    assert failed.status == "error"
    assert "voice_not_found：所选音色不存在" in failed.message
    logged = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert SECRET_TEXT not in failed.message and SECRET_TEXT not in logged


async def test_tool_is_gated_by_config_and_checks_workspace_and_cancellation_first() -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return audio_response(wav_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        registry, context, settings, _ = setup_tts(client)
        tool = registry.tools["tts__synthesize"]
        assert "tts__synthesize" in registry.available(GROUP)
        settings.tts.base_url = ""
        assert registry.permitted(GROUP, tool) == (False, "语音合成尚未填写服务地址")
        settings.tts.base_url = "http://192.168.31.100:7861"
        settings.tts.voice = ""
        assert registry.permitted(GROUP, tool) == (False, "语音合成尚未选择音色")
        settings.tts.voice = "elaina_calm"
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


def test_console_manages_local_model_voices_and_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_wav = wav_bytes(0.4)
    seen: list[tuple[str, str]] = []
    synth_payloads: list[JsonValue] = []
    model_loaded = {"value": False}

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/v1/status":
            return httpx.Response(
                200,
                json={
                    "available": True,
                    "reason": "",
                    "model_version": "IndexTTS-2.5",
                    "loaded": model_loaded["value"],
                    "device": "cuda:0 (RTX 4090)",
                    "bf16": False,
                    "vram_allocated_mb": 4200 if model_loaded["value"] else 0,
                    "vram_reserved_mb": 4600 if model_loaded["value"] else 0,
                    "voice_count": 2,
                },
            )
        if request.url.path == "/v1/model":
            body = json.loads(request.content)
            model_loaded["value"] = body["action"] == "load"
            return httpx.Response(
                200,
                json={
                    "available": True,
                    "reason": "",
                    "model_version": "IndexTTS-2.5",
                    "loaded": model_loaded["value"],
                    "device": "cuda:0 (RTX 4090)",
                    "bf16": False,
                    "vram_allocated_mb": 4200 if model_loaded["value"] else 0,
                    "vram_reserved_mb": 4600 if model_loaded["value"] else 0,
                    "voice_count": 2,
                },
            )
        if request.url.path == "/v1/voices" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {"id": "voice_a1", "name": " 伊蕾娜 ", "duration": 6.4},
                        {"id": "../bad_id", "name": "忽略", "duration": 1.0},
                        {"id": "voice_b2", "name": "傲娇参考", "duration": 4.2},
                    ]
                },
            )
        if request.url.path == "/v1/voices" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": body.get("id") or "voice_new01",
                    "name": body["name"],
                    "duration": 0.4,
                },
            )
        if request.url.path == "/v1/voices/voice_new01/sample":
            return audio_response(sample_wav)
        if request.url.path == "/v1/voices/voice_b2" and request.method == "DELETE":
            return httpx.Response(200, json={"deleted": "voice_b2"})
        if request.url.path == "/v1/synthesize":
            synth_payloads.append(json.loads(request.content))
            return audio_response(sample_wav)
        return httpx.Response(404, json={"error": "voice_not_found"})

    real_client: Callable[..., httpx.AsyncClient] = httpx.AsyncClient
    monkeypatch.setattr(
        "qq_agent_harness.tts.httpx.AsyncClient",
        lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(serve)}),
    )
    config = write_config(
        tmp_path,
        {
            "base_url": "http://192.168.31.100:7861",
            "api_key": "k1",
            "voice": "voice_a1",
        },
    )
    with TestClient(create_app(config, connect_mcp=False)) as client:
        status = client.get("/api/tts/status").json()
        assert status["available"] is True and status["loaded"] is False
        assert status["device"] == "cuda:0 (RTX 4090)"

        loaded = client.post("/api/tts/model", json={"action": "load"}).json()
        assert loaded["loaded"] is True and loaded["vram_allocated_mb"] == 4200
        unloaded = client.post("/api/tts/model", json={"action": "unload"}).json()
        assert unloaded["loaded"] is False and unloaded["vram_allocated_mb"] == 0

        listed = client.get("/api/tts/voices")
        assert listed.status_code == 200
        assert listed.json() == {
            "voices": [
                {"id": "voice_a1", "name": "伊蕾娜", "duration": 6.4},
                {"id": "voice_b2", "name": "傲娇参考", "duration": 4.2},
            ]
        }

        uploaded = client.post(
            "/api/tts/voices",
            json={
                "name": " 少女新音色 ",
                "id": "voice_new01",
                "audio": base64.b64encode(sample_wav).decode("ascii"),
            },
        )
        assert uploaded.status_code == 200
        assert uploaded.json() == {"id": "voice_new01", "name": "少女新音色", "duration": 0.4}

        audition = client.get("/api/tts/voices/voice_new01/sample")
        assert audition.status_code == 200 and audition.content == sample_wav

        # 正在使用的音色不能删除
        current = client.delete("/api/tts/voices/voice_a1")
        assert current.status_code == 409 and "正在使用" in current.json()["detail"]
        invalid = client.delete("/api/tts/voices/bad.id")
        assert invalid.status_code == 400
        deleted = client.delete("/api/tts/voices/voice_b2")
        assert deleted.status_code == 200

        # 试听支持全部情绪模式
        preview = client.post(
            "/api/tts/preview",
            json={
                "text": "哼，才不是呢",
                "voice": "voice_new01",
                "emotion_mode": "vector",
                "emotion_vector": {"angry": 0.7, "surprised": 0.3},
                "emotion_alpha": 1.1,
            },
        )
        assert preview.status_code == 200 and preview.content == sample_wav
        assert synth_payloads == [
            {
                "voice": "voice_new01",
                "text": "哼，才不是呢",
                "language": "zh",
                "emotion_mode": "vector",
                "emotion_vector": [0.0, 0.7, 0.0, 0.0, 0.0, 0.0, 0.3, 0.0],
                "emotion_alpha": 1.1,
            }
        ]


async def test_voice_upload_is_never_retried_and_listing_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("qq_agent_harness.tts.asyncio.sleep", fast_sleep)
    posts = 0
    gets = 0

    def serve(request: httpx.Request) -> httpx.Response:
        nonlocal posts, gets
        if request.method == "POST":
            posts += 1
            return httpx.Response(503, json={"error": "model_busy"})
        gets += 1
        if gets == 1:
            return httpx.Response(503, json={"error": "model_busy"})
        return httpx.Response(200, json={"voices": []})

    config = TTSSettings(base_url="http://127.0.0.1:7861", max_retries=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        upload_res = await upload_voice(config, "新音色", wav_bytes(), client=client)
        list_res = await list_voices(config, client=client)

    assert posts == 1
    assert isinstance(upload_res, ToolResult) and upload_res.status == "error"
    assert gets == 2 and list_res == []


def test_end_to_end_with_local_indextts_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证远端 MyBot 与本机 deploy/indextts/server.py 的完整端到端协议互通。"""
    server_dir = Path(__file__).resolve().parent.parent / "deploy" / "indextts"
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    import server as indextts_server  # type: ignore[import-not-found]

    class FakeIndexTTSEngine:
        def __init__(self) -> None:
            self.loaded = False
            self.calls: list[dict[str, Any]] = []

        def status(self, voice_count: int) -> dict[str, Any]:
            return {
                "available": True,
                "reason": "",
                "model_version": "IndexTTS-2.5",
                "loaded": self.loaded,
                "device": "cuda:0 (RTX 4090D)",
                "bf16": False,
                "vram_allocated_mb": 4096 if self.loaded else 0,
                "vram_reserved_mb": 4500 if self.loaded else 0,
                "voice_count": voice_count,
            }

        def load(self) -> None:
            self.loaded = True

        def unload(self) -> None:
            self.loaded = False

        def synthesize(
            self,
            spk_wav: Path,
            text: str,
            *,
            language: str,
            emotion_mode: str,
            emotion_text: str | None,
            emotion_vector: list[float] | None,
            emotion_wav: Path | None,
            emotion_alpha: float,
        ) -> bytes:
            self.loaded = True
            self.calls.append(
                {
                    "spk": spk_wav.stem,
                    "text": text,
                    "language": language,
                    "emotion_mode": emotion_mode,
                    "emotion_text": emotion_text,
                    "emotion_vector": emotion_vector,
                    "emotion_wav": emotion_wav.stem if emotion_wav else None,
                    "emotion_alpha": emotion_alpha,
                }
            )
            return wav_bytes(0.5)

    fake_engine = FakeIndexTTSEngine()
    local_app = indextts_server.create_indextts_app(
        tmp_path / "voices",
        tmp_path / "checkpoints",
        api_key="shared-secret",
        engine=fake_engine,
    )
    local_client = TestClient(local_app)

    def bridge(request: httpx.Request) -> httpx.Response:
        resp = local_client.request(
            request.method,
            request.url.path,
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(resp.status_code, headers=dict(resp.headers), content=resp.content)

    real_client: Callable[..., httpx.AsyncClient] = httpx.AsyncClient
    monkeypatch.setattr(
        "qq_agent_harness.tts.httpx.AsyncClient",
        lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(bridge)}),
    )

    config = write_config(
        tmp_path,
        {
            "enabled": True,
            "base_url": "http://192.168.31.88:7861",
            "api_key": "shared-secret",
            "voice": "anime_main",
        },
    )
    sample = wav_bytes(0.6)
    with TestClient(create_app(config, connect_mcp=False)) as client:
        # 1. 上传主音色和情绪参考音频到本机
        up1 = client.post(
            "/api/tts/voices",
            json={
                "name": "二次元女主",
                "id": "anime_main",
                "audio": base64.b64encode(sample).decode("ascii"),
            },
        )
        up2 = client.post(
            "/api/tts/voices",
            json={
                "name": "哭腔参考",
                "id": "cry_ref",
                "audio": base64.b64encode(sample).decode("ascii"),
            },
        )
        assert up1.status_code == 200 and up2.status_code == 200

        # 2. 查看状态与音色列表
        status = client.get("/api/tts/status").json()
        assert status["available"] is True and status["voice_count"] == 2
        voices = client.get("/api/tts/voices").json()["voices"]
        assert [v["id"] for v in voices] == ["anime_main", "cry_ref"]

        # 3. 试听 audio 解耦情绪模式
        prev = client.post(
            "/api/tts/preview",
            json={
                "text": "呜呜，不要欺负我",
                "language": "zh",
                "emotion_mode": "audio",
                "emotion_voice": "cry_ref",
                "emotion_alpha": 1.2,
            },
        )
        assert prev.status_code == 200
        assert fake_engine.calls == [
            {
                "spk": "anime_main",
                "text": "呜呜，不要欺负我",
                "language": "zh",
                "emotion_mode": "audio",
                "emotion_text": None,
                "emotion_vector": None,
                "emotion_wav": "cry_ref",
                "emotion_alpha": 1.2,
            }
        ]

        # 4. 远程卸载显存
        unloaded = client.post("/api/tts/model", json={"action": "unload"}).json()
        assert unloaded["loaded"] is False


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
    remember_generated(store, GeneratedMedia("big", b"x" * 300, "audio/wav"))
    assert list(store) == ["big"]


def test_tts_config_preserves_comments_and_validates_voice_id(tmp_path: Path) -> None:
    store = ConfigStore(write_config(tmp_path, {"enabled": False}))
    config = store.snapshot()["config"]
    assert isinstance(config, dict) and isinstance(config["tts"], dict)
    config["tts"]["voice"] = "../invalid"
    with pytest.raises(ConfigError) as caught:
        store.save(config, store.last_sha)
    assert caught.value.issues[0]["path"] == ["tts", "voice"]

    config["tts"]["base_url"] = "http://192.168.31.100:7861"
    config["tts"]["voice"] = "elaina_01"
    store.save(config, store.last_sha)
    text = store.path.read_text(encoding="utf-8")
    assert text.startswith("# 保留这条注释")
    reloaded = ConfigStore(store.path).settings.tts
    assert reloaded.base_url == "http://192.168.31.100:7861"
    assert reloaded.voice == "elaina_01"
