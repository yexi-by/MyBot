"""配置控制台和反向 WebSocket 接入，同端口提供 API、可选控制台鉴权与前端。"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError

from .configuration import ConfigError, ConfigStore, ConflictError
from .contracts import JSON_OBJECT, ConversationScope, JsonObject, JsonValue, Model, ToolResult
from .image import CATEGORY_LABELS as IMAGE_CATEGORIES
from .image import build_tools as image_tools
from .mcp_tools import MCPManager
from .napcat import MediaReader, NapCatError, NapCatHub, action_coverage
from .napcat import build_tools as napcat_tools
from .napcat.tools import CATEGORY_LABELS as NAPCAT_CATEGORIES
from .registry import ToolRegistry
from .runtime import AgentRuntime, SessionBusy
from .settings import TTSSettings
from .skills import CATEGORY_LABELS as SKILLS_CATEGORIES
from .skills import build_tools as skills_tools
from .subagent import CATEGORY_LABELS as SUBAGENT_CATEGORIES
from .subagent import (
    MAX_MAX_STEPS,
    MAX_TIMEOUT_SECONDS,
    MIN_MAX_STEPS,
    MIN_TIMEOUT_SECONDS,
    SubagentBusyError,
    SubagentManager,
    SubagentNotFound,
)
from .subagent import build_tools as subagent_tools
from .timers import CATEGORY_LABELS as TIMER_CATEGORIES
from .timers import TimerManager
from .timers import build_tools as timer_tools
from .tts import CATEGORY_LABELS as TTS_CATEGORIES
from .tts import (
    VOICE_PATTERN,
    EmotionMode,
    EmotionVector,
    SpeechLanguage,
    SynthesizeSpeech,
    delete_voice,
    list_voices,
    manage_model,
    service_status,
    synthesize_speech,
    upload_voice,
    voice_sample,
)
from .tts import build_tools as tts_tools
from .workspace import CATEGORY_LABELS as WORKSPACE_CATEGORIES
from .workspace import CleanupMode, WorkspaceService, WorkspaceUnavailable
from .workspace import build_tools as workspace_tools
from .workspace.executor import WorkspaceBusyError, WorkspaceOverloadedError

SESSION_COOKIE = "qah_console_session"
PUBLIC_API_PATHS = frozenset(
    {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}
)


class SaveConfig(Model):
    config: JsonObject
    sha: str


class SaveFile(Model):
    path: str
    content: str
    # None 表示只新建文件；同名文件已存在时返回冲突而不是覆盖。
    sha: str | None


class CancelTurn(Model):
    scope_key: str


class ResetSession(Model):
    scope_key: str
    # 会话正在处理时，先停止本轮再重置；否则返回冲突。
    stop: bool = False


class Cleanup(Model):
    mode: CleanupMode
    paths: list[str] = Field(default_factory=list)
    confirmed: bool = False


class LoginRequest(Model):
    token: str = Field(min_length=1)


class CancelTimerRequest(Model):
    timer_id: str = Field(min_length=1)


class CancelSubagentRequest(Model):
    subagent_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=300)


class SendSubagentRequest(Model):
    subagent_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=6000)
    extra_steps: int | None = Field(default=None, ge=MIN_MAX_STEPS, le=MAX_MAX_STEPS)
    timeout_seconds: float | None = Field(
        default=None, ge=MIN_TIMEOUT_SECONDS, le=MAX_TIMEOUT_SECONDS
    )


class PreviewSpeech(Model):
    text: str = Field(min_length=1, max_length=1000)
    language: SpeechLanguage = "zh"
    voice: str | None = Field(default=None, pattern=VOICE_PATTERN)
    emotion_mode: EmotionMode | None = None
    emotion_text: str | None = Field(default=None, min_length=1, max_length=200)
    emotion_vector: EmotionVector | None = None
    emotion_voice: str | None = Field(default=None, pattern=VOICE_PATTERN)
    emotion_alpha: float = Field(default=1.0, ge=0.0, le=1.5)


class UploadVoice(Model):
    name: str = Field(min_length=1, max_length=40)
    id: str | None = Field(default=None, pattern=VOICE_PATTERN)
    audio: str = Field(min_length=1)


class ManageTTSModel(Model):
    action: Literal["load", "unload"]


def create_app(config_path: Path, *, connect_mcp: bool = True) -> FastAPI:
    store = ConfigStore(config_path, allow_invalid=True)
    boot_id = uuid.uuid4().hex
    cookie_secret = secrets.token_bytes(32)
    started_at = time.time()
    startup = store.settings.model_copy(deep=True)
    registry = ToolRegistry(lambda: store.settings)
    registry.describe_categories(NAPCAT_CATEGORIES)
    registry.describe_categories(IMAGE_CATEGORIES)
    registry.describe_categories(TTS_CATEGORIES)
    registry.describe_categories(WORKSPACE_CATEGORIES)
    registry.describe_categories(SKILLS_CATEGORIES)
    registry.describe_categories(SUBAGENT_CATEGORIES)
    registry.describe_categories(TIMER_CATEGORIES)
    for tool in napcat_tools(startup.napcat):
        registry.add(tool)
    hub = NapCatHub(startup.napcat)
    workspace_service = WorkspaceService(lambda: store.settings.workspace, config_root=store.root)
    for tool in workspace_tools(workspace_service):
        registry.add(tool)
    runtime = AgentRuntime(store, registry, hub)
    runtime.read_workspace = workspace_service.read_file
    runtime.write_workspace = workspace_service.write_bytes
    media = MediaReader(startup.napcat)
    runtime.read_media = media.read_many
    timers = TimerManager(runtime.wake_timer)
    subagents = SubagentManager(
        lambda: store,
        registry,
        session_factory=lambda *args, **kwargs: runtime.session_factory(*args, **kwargs),
        get_napcat=hub.bot_connection,
        on_wake=runtime.wake_subagent,
        on_record=runtime.record,
        on_media=runtime.attach_generated_media,
        get_parent_session_id=runtime.session_id_for,
    )
    runtime.subagents = subagents
    for tool in image_tools(lambda: store.settings.image, startup.napcat, reader=media):
        registry.add(tool)
    for tool in tts_tools(lambda: store.settings.tts, startup.napcat):
        registry.add(tool)
    for tool in skills_tools(lambda: store):
        registry.add(tool)
    for tool in subagent_tools(subagents):
        registry.add(tool)
    for tool in timer_tools(timers):
        registry.add(tool)
    mcp = MCPManager(startup.mcp, registry)

    def session_signature(auth_token: str) -> str:
        return hmac.new(cookie_secret, auth_token.encode("utf-8"), hashlib.sha256).hexdigest()

    def is_authenticated(request: Request) -> bool:
        expected = store.settings.server.auth_token
        if not expected:
            return True
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and secrets.compare_digest(auth_header[7:], expected):
            return True
        cookie = request.cookies.get(SESSION_COOKIE, "")
        if cookie and secrets.compare_digest(cookie, session_signature(expected)):
            return True
        return False

    async def watch_config() -> None:
        invalid = False
        while True:
            await asyncio.sleep(0.5)
            try:
                changed = store.reload()
                if changed:
                    runtime.record("", "config", "配置已更新并生效")
                invalid = False
            except ConfigError, OSError, ValueError:
                if not invalid:
                    runtime.record(
                        "", "config", "配置文件格式有误，暂用上次有效配置", status="error"
                    )
                    invalid = True

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        watcher = asyncio.create_task(watch_config())
        runtime.start()
        if connect_mcp:
            await mcp.start()
        try:
            yield
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            await timers.close()
            await subagents.close()
            await runtime.close()
            await hub.close()
            await mcp.close()
            await workspace_service.close()

    app = FastAPI(title="QQ Agent Harness", lifespan=lifespan)
    app.state.store = store
    app.state.runtime = runtime
    app.state.registry = registry
    app.state.hub = hub
    app.state.workspace = workspace_service
    app.state.timers = timers
    app.state.subagents = subagents
    app.state.shutdown = None

    @app.middleware("http")
    async def console_auth_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if (
            path.startswith("/api/")
            and path not in PUBLIC_API_PATHS
            and not is_authenticated(request)
        ):
            return JSONResponse(
                {"detail": "控制台已启用访问口令，请先解锁", "auth_required": True},
                status_code=401,
            )
        return await call_next(request)

    @app.exception_handler(ConfigError)
    async def config_error(_request: Request, error: ConfigError) -> JSONResponse:
        return JSONResponse(
            {"detail": "配置无效", "issues": error.issues, "repair_path": store.path.name},
            status_code=422,
        )

    @app.exception_handler(ConflictError)
    async def conflict(_request: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": "文件已被外部修改", "sha": error.sha}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid_value(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            {"detail": "字段或响应格式无效" if isinstance(error, ValidationError) else str(error)},
            status_code=400,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation(_request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "detail": "请求字段无效",
                "issues": [
                    {"path": [str(part) for part in item["loc"]], "message": item["msg"]}
                    for item in error.errors()
                ],
            },
            status_code=422,
        )

    @app.exception_handler(FileNotFoundError)
    async def missing_file(_request: Request, _error: FileNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": "文件不存在"}, status_code=404)

    @app.exception_handler(WorkspaceBusyError)
    async def workspace_busy(_request: Request, error: WorkspaceBusyError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(WorkspaceOverloadedError)
    async def workspace_overloaded(
        _request: Request, error: WorkspaceOverloadedError
    ) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=429)

    @app.exception_handler(WorkspaceUnavailable)
    async def workspace_unavailable(_request: Request, error: WorkspaceUnavailable) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.exception_handler(httpx.HTTPError)
    async def remote_error(_request: Request, _error: httpx.HTTPError) -> JSONResponse:
        return JSONResponse({"detail": "远端服务暂不可用"}, status_code=502)

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> JsonObject:
        protected = bool(store.settings.server.auth_token)
        return {"protected": protected, "authenticated": is_authenticated(request)}

    @app.post("/api/auth/login")
    async def auth_login(payload: LoginRequest) -> JSONResponse:
        expected = store.settings.server.auth_token
        if not expected:
            return JSONResponse({"authenticated": True, "protected": False})
        if not secrets.compare_digest(payload.token, expected):
            raise HTTPException(401, "访问口令错误")
        response = JSONResponse({"authenticated": True, "protected": True})
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_signature(expected),
            httponly=True,
            samesite="lax",
            path="/",
            max_age=30 * 86_400,
        )
        return response

    @app.post("/api/auth/logout")
    async def auth_logout() -> JSONResponse:
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(key=SESSION_COOKIE, path="/")
        return response

    def restart_sections() -> list[str]:
        """启动时固定下来的设置里，当前配置已改动、需要重启进程才生效的部分。"""
        current = store.settings
        sections: list[str] = []
        if (startup.server.host, startup.server.port) != (current.server.host, current.server.port):
            sections.append("server")
        for name in ("napcat", "mcp"):
            if getattr(startup, name) != getattr(current, name):
                sections.append(name)
        if workspace_service.restart_required:
            sections.append("workspace")
        # 并发信号量和活动队列长度在创建运行器时确定。
        for name in ("concurrent_conversations", "activity_limit"):
            if getattr(startup.runtime, name) != getattr(current.runtime, name):
                sections.append(f"runtime.{name}")
        return sections

    def snapshot() -> JsonObject:
        result = store.snapshot()
        sections = restart_sections()
        result["meta"] = {
            "boot_id": boot_id,
            "revision": store.revision,
            # 控制台的原文编辑与修复入口使用实际的配置文件名。
            "config_file": store.path.name,
            "restart_required": bool(sections),
            "restart_sections": [name for name in sections],
        }
        return result

    @app.get("/api/config")
    async def read_config(response: Response) -> JsonObject:
        snap = snapshot()
        expected = store.settings.server.auth_token
        if expected:
            # 修改 auth_token 保存后自动续签当前浏览器会话 Cookie，避免操作者把自己踢出控制台。
            response.set_cookie(
                key=SESSION_COOKIE,
                value=session_signature(expected),
                httponly=True,
                samesite="lax",
                path="/",
                max_age=30 * 86_400,
            )
        return snap

    @app.put("/api/config")
    async def save_config(request: SaveConfig, response: Response) -> JsonObject:
        store.save(request.config, request.sha)
        snap = snapshot()
        expected = store.settings.server.auth_token
        if expected:
            response.set_cookie(
                key=SESSION_COOKIE,
                value=session_signature(expected),
                httponly=True,
                samesite="lax",
                path="/",
                max_age=30 * 86_400,
            )
        else:
            response.delete_cookie(key=SESSION_COOKIE, path="/")
        return snap

    @app.get("/api/files/list")
    async def list_files() -> JsonObject:
        files = sorted(
            str(path.relative_to(store.root)).replace("\\", "/")
            for path in store.root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in (".md", ".txt", ".toml")
        )
        return {"files": [name for name in files]}

    @app.get("/api/files")
    async def read_file(path: str) -> JsonObject:
        return store.read_file(path)

    @app.put("/api/files")
    async def write_file(request: SaveFile) -> JsonObject:
        return store.save_file(request.path, request.content, request.sha)

    @app.get("/api/tools")
    async def tools(kind: Literal["group", "private"] = "group", peer_id: str = "") -> JsonObject:
        return registry.catalog(
            ConversationScope(
                bot_id=startup.napcat.expected_bot_id or "preview", kind=kind, peer_id=peer_id
            )
        )

    @app.get("/api/tools/access")
    async def tool_access() -> JsonObject:
        bot_id = startup.napcat.expected_bot_id or "preview"
        scopes: JsonObject = {
            "private": [
                name
                for name in registry.available(
                    ConversationScope(bot_id=bot_id, kind="private", peer_id="")
                )
            ]
        }
        for group_id in store.settings.groups:
            scopes[f"group:{group_id}"] = [
                name
                for name in registry.available(
                    ConversationScope(bot_id=bot_id, kind="group", peer_id=group_id)
                )
            ]
        return {"scopes": scopes}

    @app.get("/api/runtime")
    async def status() -> JsonObject:
        result = runtime.snapshot()
        result["started_at"] = started_at
        result["timers"] = timers.snapshot()
        result["subagents"] = subagents.snapshot()
        result["napcat"] = {**hub.status(), "expected_bot_id": startup.napcat.expected_bot_id}
        servers: JsonObject = {}
        for name, config in store.settings.mcp.items():
            if name in mcp.statuses:
                servers[name] = {**mcp.statuses[name], "tools": len(mcp.directories.get(name, {}))}
            else:
                # 启动后才新增或启用的服务，要到重启时才会连接。
                servers[name] = {"state": "pending_restart" if config.enabled else "disabled"}
        result["mcp"] = servers
        return result

    @app.post("/api/timers/cancel")
    async def cancel_timer(request: CancelTimerRequest) -> JsonObject:
        return {"cancelled": timers.cancel(request.timer_id)}

    @app.get("/api/subagents/detail")
    async def subagent_detail(id: str, since: int | None = None) -> JsonObject:
        detail = subagents.inspect_detail(id, since=since)
        if detail is None:
            raise HTTPException(404, "子代理不存在或已被回收")
        return detail

    @app.post("/api/subagents/cancel")
    async def cancel_subagent(request: CancelSubagentRequest) -> JsonObject:
        sub_id = request.subagent_id.strip()
        if sub_id not in subagents.records:
            raise HTTPException(404, "子代理不存在或已被回收")
        cancelled = await subagents.cancel(sub_id, reason=request.reason, source="operator")
        return {"cancelled": cancelled, "subagent_id": sub_id}

    @app.post("/api/subagents/send")
    async def send_subagent(request: SendSubagentRequest) -> JsonObject:
        try:
            return await subagents.send_message(
                request.subagent_id.strip(),
                request.message,
                source="operator",
                extra_steps=request.extra_steps,
                timeout_seconds=request.timeout_seconds,
            )
        except SubagentNotFound as error:
            raise HTTPException(404, str(error)) from None
        except SubagentBusyError as error:
            raise HTTPException(409, str(error)) from None

    def console_bot() -> str:
        """控制台展示的机器人：优先配置的账号，否则取第一个在线连接。"""
        connected = [bot for bot, connection in hub.connections.items() if not connection.closed]
        expected = startup.napcat.expected_bot_id
        return expected if expected in connected else (connected[0] if connected else "")

    group_cache: dict[str, tuple[float, list[JsonValue]]] = {}

    @app.get("/api/napcat/groups")
    async def napcat_groups(refresh: bool = False) -> JsonObject:
        # 只读群目录，仅用于控制台显示群名称；不进入模型上下文，也不记录返回内容。
        bot_id = console_bot()
        if not bot_id:
            return {
                "available": False,
                "bot_id": "",
                "reason": "机器人尚未连接 NapCat",
                "groups": [],
            }
        cached = group_cache.get(bot_id)
        if cached and cached[0] > time.monotonic() and not refresh:
            return {"available": True, "bot_id": bot_id, "reason": "", "groups": cached[1]}
        try:
            data = await hub.bot_connection(bot_id).call(
                "get_group_list", {"no_cache": refresh}, timeout=10
            )
        except NapCatError, OSError:
            return {
                "available": False,
                "bot_id": bot_id,
                "reason": "读取群列表失败，请稍后重试",
                "groups": [],
            }
        groups: list[JsonValue] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not isinstance(group_id := item.get("group_id"), str):
                continue
            members, capacity = item.get("member_count"), item.get("max_member_count")
            groups.append(
                {
                    "group_id": group_id,
                    "group_name": str(item.get("group_name") or ""),
                    "member_count": members if isinstance(members, int) else None,
                    "max_member_count": capacity if isinstance(capacity, int) else None,
                }
            )
        group_cache[bot_id] = (time.monotonic() + 60, groups)
        return {"available": True, "bot_id": bot_id, "reason": "", "groups": groups}

    nickname_cache: dict[str, tuple[float, str]] = {}

    @app.get("/api/napcat/account")
    async def napcat_account() -> JsonObject:
        # 只读机器人昵称，仅用于控制台侧栏显示；读取失败时昵称留空，界面改显示 QQ 号。
        bot_id = console_bot()
        if not bot_id:
            return {"bot_id": "", "nickname": ""}
        cached = nickname_cache.get(bot_id)
        if cached and cached[0] > time.monotonic():
            return {"bot_id": bot_id, "nickname": cached[1]}
        try:
            data = await hub.bot_connection(bot_id).call("get_login_info", {}, timeout=10)
        except NapCatError, OSError:
            return {"bot_id": bot_id, "nickname": ""}
        nickname = str(data.get("nickname") or "").strip() if isinstance(data, dict) else ""
        nickname_cache[bot_id] = (time.monotonic() + 60, nickname)
        return {"bot_id": bot_id, "nickname": nickname}

    @app.get("/api/napcat/coverage")
    async def napcat_coverage() -> JsonObject:
        return action_coverage()

    @app.post("/api/runtime/cancel")
    async def cancel(request: CancelTurn) -> JsonObject:
        return {"cancelled": await runtime.cancel(request.scope_key)}

    @app.get("/api/sessions/detail")
    async def session_detail(key: str, since: int | None = None, session: str = "") -> JsonObject:
        # 只读投影内存中的可见上下文；不记录请求内容，也不写入文件。
        detail = runtime.inspect(key, since=since, session_id=session)
        if detail is None:
            raise HTTPException(404, "会话尚未建立或已被回收")
        return detail

    @app.post("/api/sessions/reset")
    async def reset_session(request: ResetSession) -> JsonObject:
        try:
            return {"session_id": await runtime.reset(request.scope_key, stop=request.stop)}
        except KeyError:
            raise HTTPException(404, "会话尚未建立或已被回收") from None
        except SessionBusy as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/workspace/status")
    async def workspace_status() -> JsonObject:
        result = await workspace_service.status()
        if result.get("restart_required"):
            result["configuration_notice"] = "工作区目录或并发上限已改动，重启服务后完全生效"
        return result

    @app.get("/api/workspace/files")
    async def workspace_files(
        path: str = "", offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1)
    ) -> JsonObject:
        return await workspace_service.files(path, offset, limit)

    @app.post("/api/workspace/cleanup")
    async def workspace_cleanup(request: Cleanup) -> JsonObject:
        return await workspace_service.cleanup(request.mode, request.paths, request.confirmed)

    @app.post("/api/workspace/cancel")
    async def workspace_cancel() -> JsonObject:
        return await workspace_service.cancel()

    model_cache: dict[str, tuple[float, object, list[str]]] = {}

    @app.get("/api/providers/{provider_id}/models")
    async def models(provider_id: str, refresh: bool = False) -> JsonObject:
        config = store.settings.providers.get(provider_id)
        if config is None:
            raise HTTPException(404, "请先保存服务商")
        fingerprint = (
            config.base_url,
            config.api_key,
            tuple(sorted(config.headers.items())),
            config.proxy,
        )
        cached = model_cache.get(provider_id)
        if cached and cached[0] > time.monotonic() and cached[1] == fingerprint and not refresh:
            return {"models": [model for model in cached[2]]}
        async with httpx.AsyncClient(
            timeout=config.timeout, proxy=config.proxy, trust_env=False
        ) as client:
            response = await client.get(
                config.endpoint_base() + "/models",
                headers=config.request_headers(),
            )
            if not response.is_success:
                raise HTTPException(502, f"模型列表接口返回 HTTP {response.status_code}")
            payload = JSON_OBJECT.validate_json(response.content)
            entries = payload.get("data")
            ids = (
                [
                    str(entry["id"])
                    for entry in entries
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                ]
                if isinstance(entries, list)
                else []
            )
        model_cache[provider_id] = (time.monotonic() + 30, fingerprint, ids)
        return {"models": [model for model in ids]}

    @app.get("/api/image/models")
    async def image_models(refresh: bool = False) -> JsonObject:
        config = store.settings.image
        if not config.base_url.strip():
            raise HTTPException(400, "请先保存生图接口地址")
        fingerprint = (
            config.protocol,
            config.base_url,
            config.api_key,
            tuple(sorted(config.headers.items())),
            config.proxy,
        )
        cached = model_cache.get("__image__")
        if cached and cached[0] > time.monotonic() and cached[1] == fingerprint and not refresh:
            return {"models": [model for model in cached[2]]}
        models_base = config.endpoint_base()
        if config.protocol == "dashscope":
            models_base = models_base.removesuffix(
                "/services/aigc/multimodal-generation/generation"
            )
        try:
            async with httpx.AsyncClient(
                timeout=config.timeout, proxy=config.proxy, trust_env=False
            ) as client:
                response = await client.get(
                    models_base + "/models",
                    headers=config.request_headers(),
                )
        except httpx.HTTPError as error:
            raise HTTPException(502, f"模型列表接口连接失败（{type(error).__name__}）") from None
        if not response.is_success:
            raise HTTPException(502, f"模型列表接口返回 HTTP {response.status_code}")
        try:
            payload = JSON_OBJECT.validate_json(response.content)
        except ValueError:
            raise HTTPException(502, "模型列表接口响应不是有效 JSON 对象") from None
        ids: list[str] = []
        if config.protocol == "gemini" and isinstance(raw_models := payload.get("models"), list):
            for entry in raw_models:
                if not isinstance(entry, dict):
                    continue
                raw_name = entry.get("name")
                raw_id = entry.get("id")
                if isinstance(raw_name, str) and raw_name:
                    ids.append(raw_name.removeprefix("models/"))
                elif isinstance(raw_id, str) and raw_id:
                    ids.append(raw_id)
        elif isinstance(entries := payload.get("data"), list):
            ids = [
                str(entry["id"])
                for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            ]
        elif isinstance(raw_models := payload.get("models"), list):
            ids = [
                str(entry["name"]).removeprefix("models/")
                for entry in raw_models
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            ]
        model_cache["__image__"] = (time.monotonic() + 30, fingerprint, ids)
        return {"models": [model for model in ids]}

    def speech_endpoint() -> TTSSettings:
        # 试听与音色管理按已保存的配置连接本机 IndexTTS，不要求先启用语音合成。
        config = store.settings.tts
        if not config.endpoint_base():
            raise HTTPException(400, "请先填写有效的本机 IndexTTS 服务地址")
        return config

    def voice_id(value: str) -> str:
        # 音色 ID 会拼进接口路径，只接受字母、数字、下划线和连字符。
        if not re.fullmatch(VOICE_PATTERN, value):
            raise HTTPException(400, "音色 ID 无效")
        return value

    def wav(audio: bytes) -> Response:
        return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.get("/api/tts/status")
    async def tts_status() -> JsonObject:
        return await service_status(store.settings.tts)

    @app.post("/api/tts/model")
    async def tts_model(request: ManageTTSModel) -> JsonObject:
        result = await manage_model(speech_endpoint(), request.action)
        if isinstance(result, ToolResult):
            raise HTTPException(502, result.message)
        return result

    @app.post("/api/tts/preview", response_model=None)
    async def tts_preview(request: PreviewSpeech) -> Response:
        # 按已保存的配置合成一小段，不经过 QQ；只返回音频，不写日志或文件。
        config = speech_endpoint()
        voice = request.voice or config.voice
        if not voice:
            raise HTTPException(400, "请先选择音色")
        try:
            synth_req = SynthesizeSpeech(
                text=request.text,
                language=request.language,
                emotion_mode=request.emotion_mode,
                emotion_text=request.emotion_text,
                emotion_vector=request.emotion_vector,
                emotion_voice=request.emotion_voice,
                emotion_alpha=request.emotion_alpha,
            )
        except ValidationError as exc:
            first = exc.errors(include_input=False, include_url=False)[0]
            raise HTTPException(400, str(first.get("msg") or "情绪控制参数无效")) from None
        result = await synthesize_speech(
            config,
            synth_req,
            voice,
            max_bytes=startup.napcat.media_max_bytes,
        )
        if isinstance(result, ToolResult):
            raise HTTPException(502, result.message)
        return wav(result.content)

    @app.get("/api/tts/voices")
    async def tts_voices() -> JsonObject:
        result = await list_voices(speech_endpoint())
        if isinstance(result, ToolResult):
            raise HTTPException(502, result.message)
        return {
            "voices": [
                {"id": item.id, "name": item.name, "duration": item.duration} for item in result
            ]
        }

    @app.post("/api/tts/voices")
    async def tts_upload_voice(request: UploadVoice) -> JsonObject:
        name = request.name.strip()
        if not name:
            raise HTTPException(400, "请填写音色名称")
        if len(request.audio) > 16 * 1024 * 1024 * 4 // 3 + 16:
            raise HTTPException(400, "参考音频超过 16 MB 上限")
        try:
            raw = base64.b64decode(request.audio, validate=True)
        except binascii.Error:
            raise HTTPException(400, "参考音频编码无效") from None
        result = await upload_voice(
            speech_endpoint(),
            name,
            raw,
            voice_id=request.id.strip() if request.id else None,
        )
        if isinstance(result, ToolResult):
            raise HTTPException(502, result.message)
        return {
            "id": result.id,
            "name": result.name,
            "duration": result.duration,
        }

    @app.get("/api/tts/voices/{voice}/sample", response_model=None)
    async def tts_voice_sample(voice: str) -> Response:
        result = await voice_sample(speech_endpoint(), voice_id(voice))
        if isinstance(result, ToolResult):
            raise HTTPException(502, result.message)
        return wav(result.content)

    @app.delete("/api/tts/voices/{voice}")
    async def tts_delete_voice(voice: str) -> JsonObject:
        config = speech_endpoint()
        if voice_id(voice) == config.voice:
            raise HTTPException(409, "正在使用的音色不能删除，请先换成其他音色")
        result = await delete_voice(config, voice)
        if isinstance(result, ToolResult):
            raise HTTPException(502, result.message)
        return {"deleted": voice}

    @app.post("/api/system/{operation}")
    async def power(operation: Literal["restart", "shutdown"]) -> JsonObject:
        callback = app.state.shutdown
        if callback is None:
            raise HTTPException(503, "进程入口尚未绑定电源控制")
        asyncio.get_running_loop().call_later(0.5, callback, operation == "restart")
        return {"accepted": True}

    @app.websocket("/napcat/ws")
    async def napcat_socket(websocket: WebSocket) -> None:
        # 空密钥不开放接入；避免全新配置被任意客户端注入机器人身份。
        if not startup.napcat.token:
            await websocket.close(code=1008, reason="请配置 NapCat token")
            return
        await hub.serve(websocket, runtime.receive)

    @app.get("/api/health")
    async def health() -> JsonObject:
        return {"ok": True, "boot_id": boot_id}

    distribution = Path(
        os.environ.get(
            "HARNESS_WEBUI_DIST", str(Path(__file__).resolve().parent.parent / "webui" / "dist")
        )
    ).resolve()
    assets = distribution / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{route:path}", response_model=None)
    async def spa(route: str) -> FileResponse:
        if route.startswith("api/") or route.startswith("napcat/"):
            raise HTTPException(404, "接口不存在")
        requested = (distribution / route).resolve()
        if requested.is_relative_to(distribution) and requested.is_file():
            return FileResponse(requested)
        index = distribution / "index.html"
        if not index.is_file():
            raise HTTPException(404, "WebUI 尚未构建；开发时请使用 Vite")
        return FileResponse(index)

    return app
