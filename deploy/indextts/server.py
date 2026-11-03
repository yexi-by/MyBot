"""本机 IndexTTS 2.5 HTTP 服务端：提供音色库管理、显存动态加载/卸载与多模式情感语音合成接口。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import gc
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from contextlib import redirect_stdout
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

VOICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
MAX_AUDIO_BYTES = 16 * 1024 * 1024


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ModelActionRequest(BaseModel):
    action: Literal["load", "unload"]


class UploadVoiceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    id: str | None = None
    audio: str = Field(min_length=1)


class SynthesizeRequest(BaseModel):
    voice: str
    text: str = Field(min_length=1, max_length=2000)
    language: Literal["zh", "en", "ja", "es", "ar"] = "zh"
    emotion_mode: Literal["reference", "text", "vector", "audio"] = "reference"
    emotion_text: str | None = None
    emotion_vector: list[Annotated[float, Field(ge=0, le=1)]] | None = Field(
        default=None, min_length=8, max_length=8
    )
    emotion_voice: str | None = None
    emotion_alpha: float = Field(default=1.0, ge=0.0, le=1.5)

    @model_validator(mode="after")
    def check_emotion(self) -> SynthesizeRequest:
        provided = {
            "text": self.emotion_text is not None,
            "vector": self.emotion_vector is not None,
            "audio": self.emotion_voice is not None,
        }
        if any(present != (mode == self.emotion_mode) for mode, present in provided.items()):
            raise ValueError("情绪模式与参数不匹配")
        if self.emotion_text is not None and not self.emotion_text.strip():
            raise ValueError("情绪描述不能为空")
        if self.emotion_vector is not None and not any(self.emotion_vector):
            raise ValueError("情感向量至少有一项大于零")
        return self


def _wav_duration(content: bytes) -> float:
    with wave.open(io.BytesIO(content), "rb") as reader:
        frames = reader.getnframes()
        rate = reader.getframerate()
        if frames <= 0 or rate <= 0:
            raise ValueError("空音频")
        return frames / rate


def _convert_to_wav(raw: bytes) -> tuple[bytes, float]:
    """校验 WAV 或调用本机 ffmpeg 将常见音频转为 24kHz 单声道 WAV。"""
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        try:
            return raw, _wav_duration(raw)
        except (wave.Error, EOFError, ValueError):
            # 浮点 WAV 同样以 RIFF/WAVE 开头，交给 ffmpeg 转成 PCM 后再校验。
            pass

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        with tempfile.TemporaryDirectory(prefix="indextts-upload-") as tmp:
            src = Path(tmp) / "input.bin"
            dst = Path(tmp) / "output.wav"
            src.write_bytes(raw)
            try:
                proc = subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-v",
                        "error",
                        "-i",
                        str(src),
                        "-ac",
                        "1",
                        "-ar",
                        "24000",
                        str(dst),
                    ],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ServiceError(400, "invalid_audio", "音频转换超时") from None
            if proc.returncode == 0 and dst.is_file():
                if dst.stat().st_size > MAX_AUDIO_BYTES:
                    raise ServiceError(400, "invalid_audio", "转换后的音频超过大小上限")
                wav_bytes = dst.read_bytes()
                try:
                    return wav_bytes, _wav_duration(wav_bytes)
                except Exception:
                    pass

    raise ServiceError(
        400,
        "invalid_audio",
        "请上传标准 WAV 音频（或在本机安装 ffmpeg 以自动转换 MP3/FLAC/OGG）",
    )


class VoiceStore:
    """管理本机 voices 目录下的参考音频与元数据。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.root / "voices.json"
        self._lock = threading.RLock()

    def _read_meta(self) -> dict[str, dict[str, Any]]:
        if not self.meta_path.is_file():
            return {}
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_meta(self, meta: dict[str, dict[str, Any]]) -> None:
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def wav_path(self, voice_id: str) -> Path:
        if not VOICE_PATTERN.fullmatch(voice_id):
            raise ServiceError(400, "invalid_voice_id", "音色 ID 格式无效")
        target = (self.root / f"{voice_id}.wav").resolve()
        if not target.is_relative_to(self.root):
            raise ServiceError(400, "invalid_voice_id", "音色路径超出目录边界")
        return target

    def list_voices(self) -> list[dict[str, Any]]:
        with self._lock:
            meta = self._read_meta()
            changed = False
            items: list[dict[str, Any]] = []
            for wav_file in sorted(self.root.glob("*.wav")):
                vid = wav_file.stem
                if not VOICE_PATTERN.fullmatch(vid):
                    continue
                entry = meta.get(vid)
                if not isinstance(entry, dict):
                    try:
                        duration = round(_wav_duration(wav_file.read_bytes()), 2)
                    except Exception:
                        continue
                    entry = {"name": vid, "duration": duration}
                    meta[vid] = entry
                    changed = True
                name = str(entry.get("name") or vid).strip() or vid
                dur = entry.get("duration")
                duration = (
                    round(float(dur), 2) if isinstance(dur, int | float) and dur >= 0 else 0.0
                )
                items.append({"id": vid, "name": name, "duration": duration})
            if changed:
                self._write_meta(meta)
            return items

    def add_voice(self, name: str, raw_audio: bytes, voice_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            clean_name = " ".join(name.split())[:100]
            if not clean_name:
                raise ServiceError(400, "invalid_voice_id", "音色名称不能为空")
            vid = (voice_id or "").strip() or f"voice_{uuid4().hex[:10]}"
            target = self.wav_path(vid)
            if target.exists():
                raise ServiceError(409, "voice_exists", "同名音色 ID 已存在")
            wav_bytes, duration = _convert_to_wav(raw_audio)
            target.write_bytes(wav_bytes)
            meta = self._read_meta()
            meta[vid] = {"name": clean_name, "duration": round(duration, 2)}
            self._write_meta(meta)
            return {"id": vid, "name": clean_name, "duration": round(duration, 2)}

    def get_sample(self, voice_id: str) -> bytes:
        target = self.wav_path(voice_id)
        if not target.is_file():
            raise ServiceError(404, "voice_not_found", "音色不存在")
        return target.read_bytes()

    def delete_voice(self, voice_id: str) -> None:
        with self._lock:
            target = self.wav_path(voice_id)
            if not target.is_file():
                raise ServiceError(404, "voice_not_found", "音色不存在")
            target.unlink()
            meta = self._read_meta()
            if voice_id in meta:
                del meta[voice_id]
                self._write_meta(meta)


class IndexTTSEngine:
    """封装 IndexTTS 2.5 推理引擎与显存生命周期。"""

    def __init__(
        self,
        model_dir: Path,
        cfg_path: Path | None = None,
        *,
        bf16: bool = False,
        use_cuda_kernel: bool = False,
        auto_load: bool = True,
    ) -> None:
        self.model_dir = model_dir.resolve()
        self.cfg_path = (cfg_path or (self.model_dir / "config.yaml")).resolve()
        self.bf16 = bf16
        self.use_cuda_kernel = use_cuda_kernel
        self.auto_load = auto_load
        self._model: Any = None
        self._lock = threading.Lock()
        self._last_error = ""

    def _gpu_info(self) -> tuple[str, int | None, int | None]:
        try:
            import torch  # type: ignore[import-not-found]

            if torch.cuda.is_available():
                name = torch.cuda.get_device_name(0)
                alloc = int(torch.cuda.memory_allocated(0) // (1024 * 1024))
                res = int(torch.cuda.memory_reserved(0) // (1024 * 1024))
                return f"cuda:0 ({name})", alloc, res
            return "cpu", None, None
        except Exception:
            return "unknown", None, None

    def status(self, voice_count: int) -> dict[str, Any]:
        device, alloc, res = self._gpu_info()
        return {
            "available": True,
            "reason": self._last_error,
            "model_version": "IndexTTS-2.5",
            "loaded": self._model is not None,
            "device": device,
            "bf16": self.bf16,
            "vram_allocated_mb": alloc,
            "vram_reserved_mb": res,
            "voice_count": voice_count,
        }

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not self.cfg_path.is_file():
                self._last_error = "未找到模型配置文件"
                raise ServiceError(503, "model_not_loaded", self._last_error)
            try:
                from indextts.infer_v2_5 import IndexTTS2  # type: ignore[import-not-found]

                self._model = IndexTTS2(
                    cfg_path=str(self.cfg_path),
                    model_dir=str(self.model_dir),
                    use_bf16=self.bf16,
                    use_cuda_kernel=self.use_cuda_kernel,
                    use_qwen_emo=True,
                )
                self._last_error = ""
            except Exception as exc:
                self._last_error = f"IndexTTS 模型加载失败：{type(exc).__name__}"
                raise ServiceError(503, "model_not_loaded", self._last_error) from None

    def unload(self) -> None:
        with self._lock:
            self._model = None
            gc.collect()
            try:
                import torch  # type: ignore[import-not-found]

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

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
        if self._model is None:
            if self.auto_load:
                self.load()
            else:
                raise ServiceError(503, "model_not_loaded", "本机 IndexTTS 模型尚未加载")

        with self._lock:
            if self._model is None:
                raise ServiceError(503, "model_not_loaded", "本机 IndexTTS 模型尚未加载")
            with tempfile.TemporaryDirectory(prefix="indextts-out-") as tmp:
                out_path = Path(tmp) / "out.wav"
                kwargs: dict[str, Any] = {
                    "spk_audio_prompt": str(spk_wav),
                    "text": text,
                    "lang": language,
                    "output_path": str(out_path),
                    "verbose": False,
                }
                if emotion_mode == "text" and emotion_text:
                    kwargs["use_emo_text"] = True
                    kwargs["emo_text"] = emotion_text
                    kwargs["emo_alpha"] = emotion_alpha
                elif emotion_mode == "vector" and emotion_vector:
                    kwargs["emo_vector"] = emotion_vector
                    kwargs["emo_alpha"] = emotion_alpha
                elif emotion_mode == "audio" and emotion_wav is not None:
                    kwargs["emo_audio_prompt"] = str(emotion_wav)
                    kwargs["emo_alpha"] = emotion_alpha
                try:
                    # 上游即使 verbose=False 也会打印情绪向量；推理输出不进入服务日志。
                    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink):
                        self._model.infer(**kwargs)
                except Exception as exc:
                    raise ServiceError(
                        500, "synthesis_failed", f"IndexTTS 推理异常：{type(exc).__name__}"
                    ) from None
                if not out_path.is_file():
                    raise ServiceError(500, "synthesis_failed", "IndexTTS 未生成音频文件")
                return out_path.read_bytes()


def create_indextts_app(
    voices_dir: Path,
    model_dir: Path,
    *,
    cfg_path: Path | None = None,
    api_key: str = "",
    bf16: bool = False,
    use_cuda_kernel: bool = False,
    auto_load: bool = True,
    engine: Any | None = None,
) -> FastAPI:
    store = VoiceStore(voices_dir)
    tts_engine = engine or IndexTTSEngine(
        model_dir,
        cfg_path,
        bf16=bf16,
        use_cuda_kernel=use_cuda_kernel,
        auto_load=auto_load,
    )

    def verify_key(authorization: str | None = Header(default=None)) -> None:
        if not api_key:
            return
        if authorization != f"Bearer {api_key}":
            raise ServiceError(401, "unauthorized", "API Key 无效")

    app = FastAPI(title="IndexTTS 2.5 Local Service", dependencies=[Depends(verify_key)])

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    @app.exception_handler(ServiceError)
    async def handle_service_error(_request: Request, error: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"error": error.code, "message": error.message},
        )

    @app.get("/v1/status")
    async def get_status() -> dict[str, Any]:
        voices = await asyncio.to_thread(store.list_voices)
        return await asyncio.to_thread(tts_engine.status, len(voices))

    @app.post("/v1/model")
    async def post_model(request: ModelActionRequest) -> dict[str, Any]:
        if request.action == "load":
            await asyncio.to_thread(tts_engine.load)
        else:
            await asyncio.to_thread(tts_engine.unload)
        voices = await asyncio.to_thread(store.list_voices)
        return await asyncio.to_thread(tts_engine.status, len(voices))

    @app.get("/v1/voices")
    async def get_voices() -> dict[str, Any]:
        voices = await asyncio.to_thread(store.list_voices)
        return {"voices": voices}

    @app.post("/v1/voices")
    async def post_voice(request: UploadVoiceRequest) -> dict[str, Any]:
        if len(request.audio) > MAX_AUDIO_BYTES * 4 // 3 + 16:
            raise ServiceError(400, "invalid_audio", "音频超过大小上限")
        try:
            raw = base64.b64decode(request.audio, validate=True)
        except binascii.Error:
            raise ServiceError(400, "invalid_audio", "音频 base64 编码无效") from None
        if not raw or len(raw) > MAX_AUDIO_BYTES:
            raise ServiceError(400, "invalid_audio", "音频大小无效")
        return await asyncio.to_thread(store.add_voice, request.name, raw, request.id)

    @app.get("/v1/voices/{voice_id}/sample", response_model=None)
    async def get_voice_sample(voice_id: str) -> Response:
        content = await asyncio.to_thread(store.get_sample, voice_id)
        return Response(content, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.delete("/v1/voices/{voice_id}")
    async def remove_voice(voice_id: str) -> dict[str, Any]:
        await asyncio.to_thread(store.delete_voice, voice_id)
        return {"deleted": voice_id}

    @app.post("/v1/synthesize", response_model=None)
    async def post_synthesize(request: SynthesizeRequest) -> Response:
        text = request.text.strip()
        if not text:
            raise ServiceError(400, "empty_text", "合成文本不能为空")
        spk_wav = store.wav_path(request.voice)
        if not spk_wav.is_file():
            raise ServiceError(404, "voice_not_found", "所选音色不存在")
        emo_wav: Path | None = None
        if request.emotion_mode == "audio":
            if not request.emotion_voice:
                raise ServiceError(400, "invalid_emotion", "缺少情绪参考音色 ID")
            emo_wav = store.wav_path(request.emotion_voice)
            if not emo_wav.is_file():
                raise ServiceError(404, "emotion_voice_not_found", "所选情绪参考音色不存在")

        wav_bytes = await asyncio.to_thread(
            tts_engine.synthesize,
            spk_wav,
            text,
            language=request.language,
            emotion_mode=request.emotion_mode,
            emotion_text=request.emotion_text,
            emotion_vector=request.emotion_vector,
            emotion_wav=emo_wav,
            emotion_alpha=request.emotion_alpha,
        )
        return Response(wav_bytes, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="本机 IndexTTS 2.5 HTTP 服务（供远端 MyBot 调用与管理）"
    )
    parser.add_argument("--host", default=os.environ.get("INDEXTTS_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("INDEXTTS_PORT", "7861")))
    parser.add_argument(
        "--indextts-root",
        type=Path,
        default=Path(os.environ.get("INDEXTTS_ROOT", ".")),
        help="本机 index-tts 仓库根目录（如未通过 pip 安装 indextts 包，会自动加入 sys.path）",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(os.environ.get("INDEXTTS_MODEL_DIR", "checkpoints")),
        help="IndexTTS 2.5 权重目录（默认 checkpoints）",
    )
    parser.add_argument(
        "--cfg-path",
        type=Path,
        default=None,
        help="配置文件路径（默认 <model-dir>/config.yaml）",
    )
    parser.add_argument(
        "--voices-dir",
        type=Path,
        default=Path(os.environ.get("INDEXTTS_VOICES_DIR", "voices")),
        help="本机保存二次元参考音频的目录（默认 voices）",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("INDEXTTS_API_KEY", ""),
        help="连接密钥（与远端 MyBot WebUI 中填写的 API Key 一致）",
    )
    parser.add_argument(
        "--bf16",
        action="store_true",
        help="启用 BF16 推理以节省显存（需要支持 BF16 的 GPU；默认 FP32）",
    )
    parser.add_argument(
        "--cuda-kernel",
        action="store_true",
        help="启用自定义 CUDA Kernel 加速",
    )
    parser.add_argument(
        "--preload",
        action="store_true",
        help="启动时立即将模型加载进显存（默认首次合成或远端面板点击加载时按需加载）",
    )
    args = parser.parse_args()

    repo_root = args.indextts_root.resolve()
    if repo_root.is_dir() and str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    model_dir = args.model_dir if args.model_dir.is_absolute() else (repo_root / args.model_dir)
    cfg_path = args.cfg_path or (model_dir / "config.yaml")

    app = create_indextts_app(
        args.voices_dir,
        model_dir,
        cfg_path=cfg_path,
        api_key=args.api_key,
        bf16=args.bf16,
        use_cuda_kernel=args.cuda_kernel,
        auto_load=True,
    )
    if args.preload:
        engine = IndexTTSEngine(
            model_dir,
            cfg_path,
            bf16=args.bf16,
            use_cuda_kernel=args.cuda_kernel,
        )
        engine.load()
        app = create_indextts_app(
            args.voices_dir,
            model_dir,
            cfg_path=cfg_path,
            api_key=args.api_key,
            bf16=args.bf16,
            use_cuda_kernel=args.cuda_kernel,
            auto_load=True,
            engine=engine,
        )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
