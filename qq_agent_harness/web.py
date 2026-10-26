"""配置控制台和反向 WebSocket 接入，同端口提供 API 与前端。"""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError

from .configuration import ConfigError, ConfigStore, ConflictError
from .contracts import JSON_OBJECT, ConversationScope, JsonObject, JsonValue, Model
from .mcp_tools import MCPManager
from .napcat import MediaReader, NapCatError, NapCatHub, action_coverage
from .napcat import build_tools as napcat_tools
from .napcat.tools import CATEGORY_LABELS as NAPCAT_CATEGORIES
from .registry import ToolRegistry
from .runtime import AgentRuntime, SessionBusy
from .sandbox.client import RunnerClient
from .sandbox.tools import CATEGORY_LABELS as SANDBOX_CATEGORIES
from .sandbox.tools import build_tools as sandbox_tools


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
    mode: Literal["cache", "selected", "all"]
    paths: list[str] = Field(default_factory=list)
    confirmed: bool = False


def create_app(config_path: Path, *, connect_mcp: bool = True) -> FastAPI:
    store = ConfigStore(config_path, allow_invalid=True)
    boot_id = uuid.uuid4().hex
    started_at = time.time()
    startup = store.settings.model_copy(deep=True)
    registry = ToolRegistry(lambda: store.settings)
    registry.describe_categories(NAPCAT_CATEGORIES)
    registry.describe_categories(SANDBOX_CATEGORIES)
    for tool in napcat_tools(startup.napcat):
        registry.add(tool)
    hub = NapCatHub(startup.napcat)
    runner = RunnerClient(lambda: startup.sandbox)
    for tool in sandbox_tools(lambda: startup.sandbox, runner):
        registry.add(tool)
    runtime = AgentRuntime(store, registry, hub)
    runtime.read_workspace = runner.read_file
    runtime.write_workspace = runner.write_bytes
    media = MediaReader(startup.napcat)
    runtime.read_media = media.read_many
    mcp = MCPManager(startup.mcp, registry)
    sandbox_pending = startup.sandbox.enabled

    async def watch_config() -> None:
        invalid = False
        while True:
            await asyncio.sleep(0.5)
            try:
                changed = store.reload()
                if changed:
                    runtime.record("", "config", "已加载有效配置；群策略立即用于后续工具调用")
                invalid = False
            except ConfigError, OSError, ValueError:
                if not invalid:
                    runtime.record(
                        "", "config", "配置文件无效；保留最后有效设置，等待原文修复", status="error"
                    )
                    invalid = True

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        watcher = asyncio.create_task(watch_config())
        if connect_mcp:
            await mcp.start()
        try:
            yield
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            await runtime.close()
            await hub.close()
            await mcp.close()
            await runner.close()

    app = FastAPI(title="QQ Agent Harness", lifespan=lifespan)
    app.state.store = store
    app.state.runtime = runtime
    app.state.registry = registry
    app.state.hub = hub
    app.state.runner = runner
    app.state.shutdown = None

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

    @app.exception_handler(httpx.HTTPStatusError)
    async def runner_status(_request: Request, error: httpx.HTTPStatusError) -> JSONResponse:
        status = error.response.status_code
        return JSONResponse(
            {
                "detail": "沙箱正在执行，请先停止后清理"
                if status == 409
                else "沙箱请求被拒绝，请检查运行状态"
            },
            status_code=status if 400 <= status < 500 else 502,
        )

    @app.exception_handler(httpx.HTTPError)
    async def remote_error(_request: Request, _error: httpx.HTTPError) -> JSONResponse:
        return JSONResponse({"detail": "远端服务暂不可用"}, status_code=502)

    def restart_sections() -> list[str]:
        """启动时固定下来的设置里，当前配置已改动、需要重启进程才生效的部分。"""
        current = store.settings
        sections = [
            name
            for name in ("server", "napcat", "mcp", "sandbox")
            if getattr(startup, name) != getattr(current, name)
        ]
        # 并发信号量和活动队列长度在创建运行器时确定。
        for name in ("concurrent_conversations", "activity_limit"):
            if getattr(startup.runtime, name) != getattr(current.runtime, name):
                sections.append(f"runtime.{name}")
        if sandbox_pending and "sandbox" not in sections:
            sections.append("sandbox")
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
            "sandbox_restart_required": sandbox_pending,
        }
        return result

    @app.get("/api/config")
    async def read_config() -> JsonObject:
        return snapshot()

    @app.put("/api/config")
    async def save_config(request: SaveConfig) -> JsonObject:
        store.save(request.config, request.sha)
        return snapshot()

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

    group_cache: dict[str, tuple[float, list[JsonValue]]] = {}

    @app.get("/api/napcat/groups")
    async def napcat_groups(refresh: bool = False) -> JsonObject:
        # 只读群目录，仅用于控制台显示群名称；不进入模型上下文，也不记录返回内容。
        connected = [bot for bot, connection in hub.connections.items() if not connection.closed]
        expected = startup.napcat.expected_bot_id
        bot_id = expected if expected in connected else (connected[0] if connected else "")
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

    @app.get("/api/sandbox/status")
    async def sandbox_status() -> JsonObject:
        nonlocal sandbox_pending
        settings = store.settings.sandbox
        try:
            result = await runner.status()
            expected = settings.model_dump(mode="json", exclude={"runner_token", "runner_url"})
            applied = result.get("applied_settings")
            sandbox_pending = settings.enabled and (
                applied != expected
                or startup.sandbox.runner_token != settings.runner_token
                or startup.sandbox.runner_url != settings.runner_url
            )
            # runner 各分支返回的字段不完全相同，这里补齐控制台依赖的字段。
            result.setdefault("used_bytes", None)
            result.setdefault("reason", "")
            result["enabled"] = settings.enabled
            result["restart_required"] = sandbox_pending
            if sandbox_pending:
                result["configuration_notice"] = (
                    "沙箱配置尚未全部应用；请重新创建 runner，应用重启不会重建沙箱控制器"
                )
            return result
        except httpx.HTTPError, ValueError:
            sandbox_pending = settings.enabled
            return {
                "enabled": settings.enabled,
                "available": False,
                "reason": "沙箱控制器不可用或尚未完成配额/网络初始化",
                "quota_bytes": settings.quota_bytes,
                "used_bytes": None,
                "busy": False,
                "restart_required": sandbox_pending,
            }

    @app.get("/api/sandbox/files")
    async def sandbox_files(path: str = "") -> JsonObject:
        return await runner.files(path)

    @app.post("/api/sandbox/cleanup")
    async def sandbox_cleanup(request: Cleanup) -> JsonObject:
        return await runner.cleanup(request.mode, request.paths, request.confirmed)

    @app.post("/api/sandbox/cancel")
    async def sandbox_cancel() -> JsonObject:
        return await runner.cancel()

    model_cache: dict[str, tuple[float, list[str]]] = {}

    @app.get("/api/providers/{provider_id}/models")
    async def models(provider_id: str, refresh: bool = False) -> JsonObject:
        config = store.settings.providers.get(provider_id)
        if config is None:
            raise HTTPException(404, "请先保存模型连接")
        cached = model_cache.get(provider_id)
        if cached and cached[0] > time.monotonic() and not refresh:
            return {"models": [model for model in cached[1]]}
        async with httpx.AsyncClient(
            timeout=config.timeout, proxy=config.proxy, trust_env=False
        ) as client:
            response = await client.get(
                config.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {config.api_key}"},
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
        model_cache[provider_id] = (time.monotonic() + 30, ids)
        return {"models": [model for model in ids]}

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
