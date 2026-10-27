"""可信控制服务：硬配额、出口防护和容器回收都先于代码执行。"""

import asyncio
import base64
import errno
import logging
import os
import secrets
import tomllib
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO, Literal, Protocol
from uuid import uuid4

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
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
    command: str = Field(min_length=1)
    cwd: str = ""
    owner: str = Field(default="", max_length=128)


class CancelRequest(Model):
    owner: str = Field(default="", max_length=128)


class WriteRequest(Model):
    path: str
    content: str
    encoding: Literal["utf8", "base64"] = "utf8"
    overwrite: bool = False
    append: bool = False


class CleanupRequest(Model):
    mode: Literal["cache", "selected", "all"]
    paths: list[str] = Field(default_factory=list)
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
        self.waiting: dict[str, tuple[str | None, asyncio.Event]] = {}
        self.active_slot: str | None = None

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
                "queued": len(self.waiting) - int(self.active_slot is not None),
                "applied_settings": applied_settings,
            }
        except (SandboxUnavailable, OSError, TimeoutError, httpx.HTTPError, ValueError) as error:
            return {
                "available": False,
                "reason": str(error)
                if isinstance(error, SandboxUnavailable)
                else f"沙箱环境检查失败（{type(error).__name__}）",
                "quota_bytes": self.settings.quota_bytes,
                "used_bytes": None,
                "busy": self.lock.locked() or self.container_id is not None,
                "execution_id": self.execution_id,
                "queued": len(self.waiting) - int(self.active_slot is not None),
                "applied_settings": applied_settings,
            }

    async def ensure_ready(self) -> None:
        await self.quota.check()
        await self.network_guard.check()

    def ensure_idle(self) -> None:
        if self.lock.locked() or self.container_id is not None or self.waiting:
            raise HTTPException(409, "共享工作区正在执行或处理文件，请先停止执行")

    async def cancel(self, owner: str = "") -> JsonObject:
        queued = matched = 0
        for identifier, (waiting_owner, event) in self.waiting.items():
            if waiting_owner is not None and (not owner or owner == waiting_owner):
                event.set()
                matched += 1
                queued += int(identifier != self.active_slot)
        running = self.execution_id is not None and (not owner or owner == self.owner)
        if running:
            self.cancel_event.set()
            if self.container_id and not self.lock.locked():
                await self.engine.remove(self.container_id)
                self.container_id = None
                self.execution_id = None
                self.owner = ""
        return {"cancelled": running or matched > 0, "queued_cancelled": queued}

    @asynccontextmanager
    async def operation_slot(self, owner: str | None) -> AsyncIterator[asyncio.Event | None]:
        if (
            len(self.waiting) - int(self.active_slot is not None)
            >= self.settings.max_pending_operations
        ):
            raise HTTPException(429, "工作区等待操作已满，请稍后重试或调整 max_pending_operations")
        identifier = uuid4().hex
        event = asyncio.Event()
        self.waiting[identifier] = (owner, event)
        acquired = asyncio.create_task(self.lock.acquire())
        stopped = asyncio.create_task(event.wait())
        try:
            await asyncio.wait({acquired, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if event.is_set():
                yield None
            else:
                if self.container_id is not None:
                    raise SandboxUnavailable("上一个执行容器尚未回收，请先停止该执行")
                self.active_slot = identifier
                yield event
        finally:
            self.waiting.pop(identifier, None)
            if self.active_slot == identifier:
                self.active_slot = None
            acquired.cancel()
            stopped.cancel()
            await asyncio.gather(acquired, stopped, return_exceptions=True)
            if not acquired.cancelled() and acquired.exception() is None and acquired.result():
                self.lock.release()

    async def execute(self, request: ExecuteRequest) -> JsonObject:
        async with self.operation_slot(request.owner) as cancel_event:
            if cancel_event is None:
                return {"status": "cancelled", "exit_code": None, "output": "", "truncated": False}
            await self.ensure_ready()
            if cancel_event.is_set():
                return {"status": "cancelled", "exit_code": None, "output": "", "truncated": False}
            cwd = self.workspace.path(request.cwd, allow_root=True)
            if not cwd.is_dir():
                raise ValueError("执行目录不存在")
            await asyncio.to_thread(self.workspace.prepare)
            self.execution_id = uuid4().hex
            self.owner = request.owner
            self.cancel_event = cancel_event
            output = bytearray()
            truncated = False
            output_complete = False
            output_path = f".outputs/{self.execution_id}.log"
            output_file: BinaryIO | None = None
            status, exit_code = "ok", -1
            container_id: str | None = None
            logs: asyncio.Task[None] | None = None
            wait: asyncio.Task[int] | None = None
            cancelled: asyncio.Task[bool] | None = None

            async def consume(identifier: str) -> None:
                nonlocal truncated
                if output_file is None:
                    raise RuntimeError("输出文件尚未建立")
                async for chunk in self.engine.logs(identifier):
                    remaining = self.settings.output_limit - len(output)
                    if len(chunk) > remaining and not truncated:
                        await asyncio.to_thread(output_file.write, bytes(output))
                        truncated = True
                    if truncated:
                        await asyncio.to_thread(output_file.write, chunk)
                    output.extend(chunk[: max(0, remaining)])

            try:
                await asyncio.to_thread(self.workspace.make_parents, output_path)
                output_file = self.workspace.path(output_path).open("xb")
                if cancel_event.is_set():
                    output_complete = True
                    return {
                        "status": "cancelled",
                        "output": "",
                        "exit_code": None,
                        "truncated": False,
                    }
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
                if cancel_event.is_set():
                    output_complete = True
                    return {
                        "status": "cancelled",
                        "output": "",
                        "exit_code": None,
                        "truncated": False,
                    }
                await self.engine.start(container_id)
                deadline = asyncio.get_running_loop().time() + self.settings.command_timeout
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
                        drained, _ = await asyncio.wait(
                            {logs, cancelled},
                            timeout=max(0, deadline - asyncio.get_running_loop().time()),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if cancelled in drained:
                            status = "cancelled"
                        elif logs in drained:
                            logs.result()
                            output_complete = True
                    except TimeoutError, OSError, httpx.HTTPError:
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
                try:
                    if output_file is not None and not output_complete and not truncated:
                        await asyncio.to_thread(output_file.write, bytes(output))
                        truncated = True
                except OSError:
                    output_complete = False
                    truncated = True
                finally:
                    if output_file is not None:
                        try:
                            await asyncio.to_thread(output_file.close)
                        except OSError:
                            output_complete = False
                            truncated = True
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
                if output_file is not None and not truncated:
                    try:
                        await asyncio.to_thread(self.workspace.cleanup, "selected", [output_path])
                    except ValueError, OSError:
                        logger.warning("sandbox_output_cleanup_failed")
            return {
                "status": status,
                "execution_id": execution_id,
                "exit_code": exit_code,
                "output": output.decode("utf-8", errors="replace"),
                "truncated": truncated,
                "output_complete": output_complete,
                "output_path": output_path if truncated else None,
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
    async def unavailable(_request: object, error: SandboxUnavailable) -> Response:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"detail": str(error), "error_type": "SandboxUnavailable"}, status_code=503
        )

    @app.exception_handler(ValueError)
    async def invalid(_request: object, error: ValueError) -> Response:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(OSError)
    async def filesystem_error(_request: object, error: OSError) -> Response:
        from fastapi.responses import JSONResponse

        code, detail = {
            errno.ENOENT: ("FileNotFound", "文件或目录不存在"),
            errno.EEXIST: ("FileExists", "目标文件已存在；明确覆盖或追加后再写入"),
            errno.ENOSPC: ("StorageFull", "磁盘空间不足"),
            errno.EDQUOT: ("QuotaExceeded", "工作区配额已用尽，可清理文件后重试"),
            errno.EACCES: ("AccessDenied", "文件权限不允许此操作"),
        }.get(error.errno or 0, ("FilesystemError", "文件操作失败"))
        return JSONResponse({"detail": detail, "error_type": code}, status_code=400)

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
    async def files(
        path: str = "", offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1)
    ) -> JsonObject:
        async with runner.operation_slot(None):
            return await asyncio.to_thread(runner.workspace.files, path, offset, limit)

    @app.get("/file/text")
    async def read_text(
        path: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=65_536, ge=1)
    ) -> JsonObject:
        async with runner.operation_slot(None):
            return await asyncio.to_thread(
                runner.workspace.read_text,
                path,
                offset,
                min(limit, runner.settings.transfer_max_bytes),
            )

    @app.get("/file")
    async def read(path: str) -> Response:
        async with runner.operation_slot(None):
            content = await asyncio.to_thread(
                runner.workspace.read, path, runner.settings.transfer_max_bytes
            )
            return Response(content, media_type="application/octet-stream")

    @app.put("/file")
    async def write(payload: WriteRequest, request: Request) -> JsonObject:
        async with runner.operation_slot(None):
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未写入")
            await runner.quota.check()
            content = (
                payload.content.encode("utf-8")
                if payload.encoding == "utf8"
                else base64.b64decode(payload.content, validate=True)
            )
            if len(content) > runner.settings.transfer_max_bytes:
                raise HTTPException(413, "内容超过单次传输预算；请分段追加")
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未写入")
            await asyncio.to_thread(
                runner.workspace.write,
                payload.path,
                content,
                overwrite=payload.overwrite,
                append=payload.append,
            )
            return {"path": payload.path, "bytes": len(content)}

    @app.post("/file/content")
    async def write_content(request: Request, path: str) -> JsonObject:
        async with runner.operation_slot(None):
            await runner.quota.check()
            runner.workspace.path(path)
            content = bytearray()
            async for chunk in request.stream():
                if len(content) + len(chunk) > runner.settings.transfer_max_bytes:
                    raise HTTPException(413, "传入文件超过已配置的单次传输预算")
                content.extend(chunk)
            # 仅创建已经完成边界校验的父路径；整段操作期间没有执行容器。
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未写入")
            await asyncio.to_thread(runner.workspace.make_parents, path)
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未写入")
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
