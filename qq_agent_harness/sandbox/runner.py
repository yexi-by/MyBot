"""可信控制服务：硬配额、出口防护和容器回收都先于代码执行。"""

import asyncio
import base64
import logging
import os
import secrets
import tomllib
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import Field

from ..contracts import JSON_OBJECT, JsonObject, Model
from ..settings import SandboxSettings
from .docker import DockerEngine, Engine, container_config
from .quota import BtrfsQuota, NetworkGuard, QuotaStatus, SandboxUnavailable
from .workspace import Workspace

logger = logging.getLogger(__name__)


class QuotaCheck(Protocol):
    async def check(self) -> QuotaStatus: ...


class GuardCheck(Protocol):
    async def check(self) -> None: ...


class ExecuteRequest(Model):
    command: str = Field(min_length=1, max_length=100_000)
    cwd: str = ""
    owner: str = Field(default="", max_length=128)


class CancelRequest(Model):
    owner: str = Field(default="", max_length=128)


class WriteRequest(Model):
    path: str
    content: str = Field(max_length=8_000_000)
    encoding: Literal["utf8", "base64"] = "utf8"
    overwrite: bool = False


class CleanupRequest(Model):
    mode: Literal["cache", "selected", "all"]
    paths: list[str] = Field(default_factory=list, max_length=1000)
    confirmed: bool = False


class Runner:
    def __init__(
        self,
        *,
        workspace: Workspace,
        quota: QuotaCheck,
        network_guard: GuardCheck,
        engine: Engine,
        settings: SandboxSettings,
        host_workspace: str,
        image: str = "qq-agent-harness-sandbox:1.0.0",
        network: str = "qah-sandbox",
        proxy: str = "http://172.30.240.2:3128",
    ):
        self.workspace, self.quota, self.network_guard = workspace, quota, network_guard
        self.engine, self.settings = engine, settings.model_copy(deep=True)
        self.host_workspace, self.image, self.network, self.proxy = (
            host_workspace,
            image,
            network,
            proxy,
        )
        self.lock = asyncio.Lock()
        self.execution_id: str | None = None
        self.container_id: str | None = None
        self.cancel_event = asyncio.Event()
        self.owner = ""

    async def status(self) -> JsonObject:
        applied_settings = JSON_OBJECT.validate_python(
            self.settings.model_dump(mode="json", exclude={"runner_token", "runner_url"})
        )
        try:
            if self.container_id and not self.lock.locked():
                raise SandboxUnavailable("执行容器尚未成功回收")
            quota = await self.quota.check()
            await self.network_guard.check()
            return {
                "available": True,
                "reason": "",
                "quota_bytes": quota.quota_bytes,
                "used_bytes": quota.used_bytes,
                "busy": self.lock.locked() or self.container_id is not None,
                "execution_id": self.execution_id,
                "applied_settings": applied_settings,
            }
        except SandboxUnavailable, OSError, TimeoutError, httpx.HTTPError, ValueError:
            return {
                "available": False,
                "reason": "硬配额或网络隔离未就绪，请检查部署环境",
                "quota_bytes": self.settings.quota_bytes,
                "used_bytes": None,
                "busy": self.lock.locked() or self.container_id is not None,
                "execution_id": self.execution_id,
                "applied_settings": applied_settings,
            }

    async def ensure_ready(self) -> None:
        await self.quota.check()
        await self.network_guard.check()

    def ensure_idle(self) -> None:
        if self.lock.locked() or self.container_id is not None:
            raise HTTPException(409, "共享工作区正在执行或处理文件，请先停止执行")

    async def cancel(self, owner: str = "") -> JsonObject:
        running = self.execution_id is not None and (not owner or owner == self.owner)
        if running:
            self.cancel_event.set()
            if self.container_id and not self.lock.locked():
                await self.engine.remove(self.container_id)
                self.container_id = None
                self.execution_id = None
                self.owner = ""
        return {"cancelled": running}

    async def execute(self, request: ExecuteRequest) -> JsonObject:
        self.ensure_idle()
        async with self.lock:
            await self.ensure_ready()
            cwd = self.workspace.path(request.cwd, allow_root=True)
            if not cwd.is_dir():
                raise ValueError("执行目录不存在")
            await asyncio.to_thread(self.workspace.prepare)
            self.execution_id = uuid4().hex
            self.owner = request.owner
            self.cancel_event = asyncio.Event()
            output = bytearray()
            truncated = False
            status, exit_code = "ok", -1
            container_id: str | None = None
            logs: asyncio.Task[None] | None = None
            wait: asyncio.Task[int] | None = None
            cancelled: asyncio.Task[bool] | None = None

            async def consume(identifier: str) -> None:
                nonlocal truncated
                async for chunk in self.engine.logs(identifier):
                    remaining = self.settings.output_limit - len(output)
                    output.extend(chunk[: max(0, remaining)])
                    if len(chunk) > remaining:
                        truncated = True

            try:
                payload = container_config(
                    command=request.command,
                    cwd=request.cwd,
                    image=self.image,
                    host_workspace=self.host_workspace,
                    network=self.network,
                    proxy=self.proxy,
                    settings=self.settings,
                )
                container_id = await self.engine.create(f"qah-exec-{self.execution_id}", payload)
                self.container_id = container_id
                await self.engine.start(container_id)
                logs = asyncio.create_task(consume(container_id))
                wait = asyncio.create_task(self.engine.wait(container_id))
                cancelled = asyncio.create_task(self.cancel_event.wait())
                # 显式收集并回收三个协程，不能让 stdout 或 wait 在取消后悬挂。
                done, _ = await asyncio.wait(
                    {wait, cancelled},
                    timeout=self.settings.command_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancelled in done:
                    status = "cancelled"
                elif wait in done:
                    exit_code = wait.result()
                    status = "ok" if exit_code == 0 else "error"
                    try:
                        async with asyncio.timeout(5):
                            await logs
                    except TimeoutError:
                        logs.cancel()
                else:
                    status = "timeout"
                for task in (logs, wait, cancelled):
                    task.cancel()
                await asyncio.gather(logs, wait, cancelled, return_exceptions=True)
            finally:
                for task in (logs, wait, cancelled):
                    if task is not None:
                        task.cancel()
                pending: list[Awaitable[object]] = []
                for task in (logs, wait, cancelled):
                    if task is not None:
                        pending.append(task)
                await asyncio.gather(*pending, return_exceptions=True)
                removed = False
                try:
                    if container_id is not None:
                        # force 删除执行容器也清理衍生/后台进程；共享 bind 目录不删除。
                        await asyncio.shield(self.engine.remove(container_id))
                    removed = True
                finally:
                    execution_id = self.execution_id
                    if removed:
                        self.container_id = None
                        self.execution_id = None
                        self.owner = ""
            return {
                "status": status,
                "execution_id": execution_id,
                "exit_code": exit_code,
                "output": output.decode("utf-8", errors="replace"),
                "truncated": truncated,
            }

    async def close(self) -> None:
        await self.cancel()
        if self.container_id:
            await self.engine.remove(self.container_id)
        await self.engine.close()


def create_app(runner: Runner, token: str) -> FastAPI:
    if len(token) < 24:
        raise ValueError("Runner token 至少需要 24 个字符，不能以无认证模式启动")

    async def authorize(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not secrets.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "Runner 凭据无效")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await runner.engine.prune_managed()
        yield
        await runner.close()

    app = FastAPI(
        title="QQ Harness trusted runner",
        lifespan=lifespan,
        dependencies=[Depends(authorize)],
        docs_url=None,
        redoc_url=None,
    )

    @app.exception_handler(SandboxUnavailable)
    async def unavailable(_request: object, _error: SandboxUnavailable) -> Response:
        return Response(
            '{"detail":"沙箱硬配额或网络隔离不可用"}',
            status_code=503,
            media_type="application/json",
        )

    @app.exception_handler(ValueError)
    async def invalid(_request: object, error: ValueError) -> Response:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(OSError)
    async def filesystem_error(_request: object, _error: OSError) -> Response:
        return Response(
            '{"detail":"文件操作失败：请检查路径、容量和权限"}',
            status_code=400,
            media_type="application/json",
        )

    @app.exception_handler(httpx.HTTPError)
    async def engine_error(_request: object, _error: httpx.HTTPError) -> Response:
        return Response(
            '{"detail":"隔离执行服务调用失败"}', status_code=503, media_type="application/json"
        )

    @app.get("/status")
    async def status() -> JsonObject:
        return await runner.status()

    @app.post("/exec")
    async def execute(request: ExecuteRequest) -> JsonObject:
        return await runner.execute(request)

    @app.post("/cancel")
    async def cancel(request: CancelRequest) -> JsonObject:
        return await runner.cancel(request.owner)

    @app.get("/files")
    async def files(path: str = "") -> JsonObject:
        runner.ensure_idle()
        async with runner.lock:
            return await asyncio.to_thread(runner.workspace.files, path)

    @app.get("/file")
    async def read(path: str) -> Response:
        runner.ensure_idle()
        async with runner.lock:
            content = await asyncio.to_thread(runner.workspace.read, path)
            return Response(content, media_type="application/octet-stream")

    @app.put("/file")
    async def write(request: WriteRequest) -> JsonObject:
        runner.ensure_idle()
        async with runner.lock:
            await runner.quota.check()
            content = (
                request.content.encode("utf-8")
                if request.encoding == "utf8"
                else base64.b64decode(request.content, validate=True)
            )
            await asyncio.to_thread(
                runner.workspace.write, request.path, content, overwrite=request.overwrite
            )
            return {"path": request.path, "bytes": len(content)}

    @app.post("/file/content")
    async def write_content(request: Request, path: str) -> JsonObject:
        runner.ensure_idle()
        async with runner.lock:
            await runner.quota.check()
            runner.workspace.path(path)
            content = bytearray()
            async for chunk in request.stream():
                if len(content) + len(chunk) > 50_000_000:
                    raise HTTPException(413, "传入文件超过单次 50 MB 限制")
                content.extend(chunk)
            # 仅创建已经完成边界校验的父路径；整段操作期间没有执行容器。
            await asyncio.to_thread(runner.workspace.make_parents, path)
            await asyncio.to_thread(runner.workspace.write, path, bytes(content))
            return {"path": path, "bytes": len(content)}

    @app.post("/cleanup")
    async def cleanup(request: CleanupRequest) -> JsonObject:
        if not request.confirmed:
            raise HTTPException(400, "清理前必须确认影响范围")
        runner.ensure_idle()
        async with runner.lock:
            removed = await asyncio.to_thread(runner.workspace.cleanup, request.mode, request.paths)
        return JSON_OBJECT.validate_python({"removed": removed, **await runner.status()})

    return app


def main() -> None:
    config_path = os.environ.get("HARNESS_CONFIG", "/app/config/harness.toml")
    with Path(config_path).open("rb") as stream:
        raw = tomllib.load(stream)
    settings = SandboxSettings.model_validate(raw.get("sandbox", {}))
    workspace_path = Path(os.environ.get("HARNESS_WORKSPACE", "/workspace-data"))
    host_workspace = os.environ["HARNESS_WORKSPACE_HOST"]
    engine = DockerEngine(host_workspace)

    async def inspect_network() -> JsonObject:
        return await engine.network_info("qah-sandbox")

    runner = Runner(
        workspace=Workspace(workspace_path),
        quota=BtrfsQuota(workspace_path, settings.quota_bytes),
        network_guard=NetworkGuard("br-qah-sbx", "172.30.240.2", 3128, inspect_network),
        engine=engine,
        settings=settings,
        host_workspace=host_workspace,
        image=os.environ.get("HARNESS_SANDBOX_IMAGE", "qq-agent-harness-sandbox:1.0.0"),
    )
    uvicorn.run(
        create_app(runner, settings.runner_token), host="127.0.0.1", port=6060, access_log=False
    )


if __name__ == "__main__":
    main()
