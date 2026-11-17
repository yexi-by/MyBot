"""可信控制服务：硬配额、出口防护、独立临时空间和容器回收都先于代码执行。"""

import asyncio
import base64
import errno
import logging
import os
import secrets
import tomllib
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
    timeout: float | None = Field(default=None, gt=0)


class CancelRequest(Model):
    owner: str = Field(default="", max_length=128)


class WriteRequest(Model):
    path: str
    content: str
    encoding: Literal["utf8", "base64"] = "utf8"
    overwrite: bool = False
    append: bool = False


class EditRequest(Model):
    path: str
    old_string: str = Field(min_length=1)
    new_string: str
    replace_all: bool = False


class DeleteRequest(Model):
    path: str
    recursive: bool = False


CleanupMode = Literal["temp", "cache", "selected", "all"]


class CleanupRequest(Model):
    mode: CleanupMode
    paths: list[str] = Field(default_factory=list)
    confirmed: bool = False


def _trim_utf8_end(data: bytes) -> bytes:
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError as error:
        if error.reason == "unexpected end of data":
            return data[: error.start]
        return data


def _trim_utf8_start(data: bytes) -> bytes:
    index = 0
    limit = min(len(data), 4)
    while index < limit and (data[index] & 0xC0) == 0x80:
        index += 1
    return data[index:]


class HeadTailRetainer:
    """字节流头尾双端保留器：不超限时完整保留，超限时保留头尾并标明中间省略字节。"""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.total = 0
        self.head = bytearray()
        self.tail = bytearray()

    @property
    def truncated(self) -> bool:
        return self.total > self.limit

    def push(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total += len(chunk)
        if len(self.head) < self.limit:
            need = self.limit - len(self.head)
            self.head.extend(chunk[:need])
        if len(chunk) >= self.limit:
            self.tail = bytearray(chunk[-self.limit :])
        else:
            overflow = len(self.tail) + len(chunk) - self.limit
            if overflow > 0:
                del self.tail[:overflow]
            self.tail.extend(chunk)

    def render(self, output_path: str) -> str:
        if not self.truncated:
            return bytes(self.head).decode("utf-8", errors="replace")

        def make_notice(omitted: int) -> bytes:
            return f"\n... [中间省略 {omitted} 字节，完整输出见 {output_path}] ...\n".encode()

        max_notice = make_notice(self.total)
        if len(max_notice) + 32 > self.limit:
            return _trim_utf8_end(bytes(self.head[: self.limit])).decode("utf-8", errors="replace")
        available = self.limit - len(max_notice)
        head_budget = available // 2
        tail_budget = available - head_budget
        head_bytes = _trim_utf8_end(bytes(self.head[:head_budget]))
        tail_bytes = _trim_utf8_start(bytes(self.tail[-tail_budget:])) if tail_budget > 0 else b""
        omitted = max(0, self.total - len(head_bytes) - len(tail_bytes))
        notice = make_notice(omitted)
        return (
            head_bytes.decode("utf-8", errors="replace")
            + notice.decode("utf-8")
            + tail_bytes.decode("utf-8", errors="replace")
        )


@dataclass
class _ActiveExecution:
    execution_id: str
    owner: str
    cancel_event: asyncio.Event
    container_id: str | None = None


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
        self.semaphore = asyncio.Semaphore(max(1, self.settings.concurrent_executions))
        self.waiting: dict[str, tuple[str | None, asyncio.Event]] = {}
        self.active_slots: set[str] = set()
        self.active: dict[str, _ActiveExecution] = {}
        self.unreclaimed: dict[str, str] = {}
        self.active_file_ops = 0
        self.cleaning = False

    @property
    def execution_id(self) -> str | None:
        first = next(iter(self.active.values()), None)
        return first.execution_id if first is not None else None

    @property
    def is_busy(self) -> bool:
        return bool(self.active_slots or self.active or self.unreclaimed)

    async def status(self) -> JsonObject:
        applied_settings = JSON_OBJECT.validate_python(
            self.settings.model_dump(mode="json", exclude={"runner_token", "runner_url"})
        )
        try:
            if self.unreclaimed:
                raise SandboxUnavailable("执行容器尚未成功回收")
            quota = await self.quota.check()
            await self.network_guard.check()
            return {
                "available": True,
                "reason": "",
                "quota_bytes": quota.quota_bytes,
                "used_bytes": quota.used_bytes,
                "busy": self.is_busy,
                "active_count": len(self.active),
                "execution_id": self.execution_id,
                "queued": len(self.waiting),
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
                "busy": self.is_busy,
                "active_count": len(self.active),
                "execution_id": self.execution_id,
                "queued": len(self.waiting),
                "applied_settings": applied_settings,
            }

    async def ensure_ready(self) -> None:
        await self.quota.check()
        await self.network_guard.check()

    def ensure_idle(self) -> None:
        if self.is_busy or self.waiting or self.active_file_ops > 0 or self.cleaning:
            raise HTTPException(409, "共享工作区正在执行或处理文件，请先停止执行")

    @asynccontextmanager
    async def file_slot(self) -> AsyncIterator[None]:
        if self.cleaning:
            raise HTTPException(409, "共享工作区正在清理文件，请稍后重试")
        if self.active_file_ops >= self.settings.max_pending_operations:
            raise HTTPException(429, "工作区并发文件操作已满，请稍后重试")
        self.active_file_ops += 1
        try:
            yield
        finally:
            self.active_file_ops -= 1

    async def cancel(self, owner: str = "") -> JsonObject:
        queued = matched = 0
        for waiting_owner, event in list(self.waiting.values()):
            if waiting_owner is not None and (not owner or owner == waiting_owner):
                event.set()
                matched += 1
                queued += 1
        running = False
        for item in list(self.active.values()):
            if not owner or owner == item.owner:
                item.cancel_event.set()
                running = True
        for cid, stale_owner in list(self.unreclaimed.items()):
            if not owner or owner == stale_owner:
                await self.engine.remove(cid)
                self.unreclaimed.pop(cid, None)
                running = True
        return {"cancelled": running or matched > 0, "queued_cancelled": queued}

    @asynccontextmanager
    async def operation_slot(
        self, owner: str | None
    ) -> AsyncIterator[tuple[str, asyncio.Event] | None]:
        if self.cleaning:
            raise HTTPException(409, "共享工作区正在清理文件，请稍后重试")
        if len(self.waiting) >= self.settings.max_pending_operations:
            raise HTTPException(429, "工作区等待操作已满，请稍后重试或调整 max_pending_operations")
        identifier = uuid4().hex
        event = asyncio.Event()
        self.waiting[identifier] = (owner, event)
        acquired = asyncio.create_task(self.semaphore.acquire())
        stopped = asyncio.create_task(event.wait())
        holds_slot = False
        try:
            await asyncio.wait({acquired, stopped}, return_when=asyncio.FIRST_COMPLETED)
            self.waiting.pop(identifier, None)
            if not acquired.done():
                acquired.cancel()
                await asyncio.gather(acquired, return_exceptions=True)
            if acquired.done() and not acquired.cancelled() and acquired.exception() is None:
                holds_slot = bool(acquired.result())
            if event.is_set():
                yield None
            else:
                if self.cleaning:
                    raise HTTPException(409, "共享工作区正在清理文件，请稍后重试")
                if self.unreclaimed:
                    raise SandboxUnavailable("上一个执行容器尚未回收，请先停止该执行")
                self.active_slots.add(identifier)
                yield identifier, event
        finally:
            self.waiting.pop(identifier, None)
            self.active_slots.discard(identifier)
            stopped.cancel()
            if not acquired.done():
                acquired.cancel()
            results = await asyncio.gather(acquired, stopped, return_exceptions=True)
            if holds_slot or (results and results[0] is True):
                self.semaphore.release()
                holds_slot = False

    async def execute(self, request: ExecuteRequest) -> JsonObject:
        async with self.operation_slot(request.owner) as slot:
            if slot is None:
                return {"status": "cancelled", "exit_code": None, "output": "", "truncated": False}
            slot_id, cancel_event = slot
            await self.ensure_ready()
            if cancel_event.is_set():
                return {"status": "cancelled", "exit_code": None, "output": "", "truncated": False}
            cwd = self.workspace.path(request.cwd, allow_root=True)
            if not cwd.is_dir():
                raise ValueError("执行目录不存在")
            execution_id = uuid4().hex
            active = _ActiveExecution(
                execution_id=execution_id,
                owner=request.owner,
                cancel_event=cancel_event,
            )
            self.active[slot_id] = active
            retainer = HeadTailRetainer(self.settings.output_limit)
            spilled = False
            output_complete = False
            output_path = f".outputs/{execution_id}.log"
            output_file: BinaryIO | None = None
            status, exit_code = "ok", -1
            container_id: str | None = None
            logs: asyncio.Task[None] | None = None
            wait: asyncio.Task[int] | None = None
            cancelled: asyncio.Task[bool] | None = None

            async def consume(identifier: str) -> None:
                nonlocal spilled
                if output_file is None:
                    raise RuntimeError("输出文件尚未建立")
                async for chunk in self.engine.logs(identifier):
                    was_truncated = retainer.truncated
                    previous_head = bytes(retainer.head) if not was_truncated else b""
                    retainer.push(chunk)
                    if retainer.truncated and not was_truncated:
                        await asyncio.to_thread(output_file.write, previous_head + chunk)
                        spilled = True
                    elif retainer.truncated:
                        await asyncio.to_thread(output_file.write, chunk)

            try:
                await asyncio.to_thread(self.workspace.create_exec_tmp, execution_id)
                output_file = await asyncio.to_thread(
                    self.workspace.open_exclusive_binary, output_path
                )
                if cancel_event.is_set():
                    output_complete = True
                    return {
                        "status": "cancelled",
                        "output": "",
                        "exit_code": None,
                        "truncated": False,
                    }
                exec_tmp_host = f"{self.host_workspace.rstrip('/')}/.tmp/exec-{execution_id}"
                payload = container_config(
                    command=request.command,
                    cwd=request.cwd,
                    image=self.image,
                    host_workspace=self.host_workspace,
                    network=self.network,
                    proxy=self.proxy,
                    settings=self.settings,
                    exec_tmp_host=exec_tmp_host,
                )
                container_id = await self.engine.create(f"qah-exec-{execution_id}", payload)
                active.container_id = container_id
                if cancel_event.is_set():
                    output_complete = True
                    return {
                        "status": "cancelled",
                        "output": "",
                        "exit_code": None,
                        "truncated": False,
                    }
                await self.engine.start(container_id)
                effective_timeout = (
                    request.timeout
                    if request.timeout is not None
                    else self.settings.command_timeout
                )
                deadline = asyncio.get_running_loop().time() + effective_timeout
                logs = asyncio.create_task(consume(container_id))
                wait = asyncio.create_task(self.engine.wait(container_id))
                cancelled = asyncio.create_task(cancel_event.wait())
                # 显式收集并回收三个协程，不能让 stdout 或 wait 在取消后悬挂。
                done, _ = await asyncio.wait(
                    {wait, cancelled},
                    timeout=effective_timeout,
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
                pending: list[Awaitable[object]] = [
                    task for task in (logs, wait, cancelled) if task is not None
                ]
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if output_file is not None:
                    try:
                        await asyncio.to_thread(output_file.close)
                    except OSError:
                        output_complete = False
                removed = False
                try:
                    if container_id is not None:
                        # force 删除执行容器也清理衍生/后台进程；随后回收当次 /tmp 目录。
                        await asyncio.shield(self.engine.remove(container_id))
                    removed = True
                finally:
                    self.active.pop(slot_id, None)
                    if container_id is not None and not removed:
                        self.unreclaimed[container_id] = request.owner
                if removed:
                    await asyncio.to_thread(self.workspace.remove_exec_tmp, execution_id)
                if output_file is not None and not spilled:
                    try:
                        await asyncio.to_thread(self.workspace.cleanup, "selected", [output_path])
                    except ValueError, OSError:
                        logger.warning("sandbox_output_cleanup_failed")
                elif spilled:
                    await asyncio.to_thread(self.workspace.prune_outputs)
            return {
                "status": status,
                "execution_id": execution_id,
                "exit_code": exit_code,
                "output": retainer.render(output_path),
                "truncated": retainer.truncated,
                "output_complete": output_complete,
                "output_path": output_path if spilled else None,
            }

    async def close(self) -> None:
        await self.cancel()
        for item in list(self.active.values()):
            if item.container_id:
                await self.engine.remove(item.container_id)
        for cid in list(self.unreclaimed):
            await self.engine.remove(cid)
            self.unreclaimed.pop(cid, None)
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
        await asyncio.to_thread(runner.workspace.prepare, clean_stale_exec_tmp=True)
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

        return JSONResponse(
            {"detail": str(error), "error_type": "InvalidOperation"}, status_code=400
        )

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
        async with runner.file_slot():
            return await asyncio.to_thread(runner.workspace.files, path, offset, limit)

    @app.get("/files/search")
    async def search_files(
        query: str = "",
        path: str = "",
        glob: str = "*",
        regex: bool = False,
        case_sensitive: bool = False,
        include_hidden: bool = False,
        max_results: int = Query(default=100, ge=1, le=500),
    ) -> JsonObject:
        async with runner.file_slot():
            return await asyncio.to_thread(
                runner.workspace.search,
                query,
                relative=path,
                glob_pattern=glob,
                regex=regex,
                case_sensitive=case_sensitive,
                include_hidden=include_hidden,
                max_results=max_results,
            )

    @app.get("/file/text")
    async def read_text(
        path: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=65_536, ge=1)
    ) -> JsonObject:
        async with runner.file_slot():
            return await asyncio.to_thread(
                runner.workspace.read_text,
                path,
                offset,
                min(limit, runner.settings.transfer_max_bytes),
            )

    @app.get("/file")
    async def read(path: str) -> Response:
        async with runner.file_slot():
            content = await asyncio.to_thread(
                runner.workspace.read, path, runner.settings.transfer_max_bytes
            )
            return Response(content, media_type="application/octet-stream")

    @app.put("/file")
    async def write(payload: WriteRequest, request: Request) -> JsonObject:
        async with runner.file_slot():
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

    @app.post("/file/edit")
    async def edit(payload: EditRequest, request: Request) -> JsonObject:
        async with runner.file_slot():
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未修改")
            await runner.quota.check()
            return await asyncio.to_thread(
                runner.workspace.edit_text,
                payload.path,
                payload.old_string,
                payload.new_string,
                replace_all=payload.replace_all,
                max_bytes=runner.settings.transfer_max_bytes,
            )

    @app.post("/file/delete")
    async def delete(payload: DeleteRequest, request: Request) -> JsonObject:
        async with runner.file_slot():
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未删除")
            return await asyncio.to_thread(
                runner.workspace.delete, payload.path, recursive=payload.recursive
            )

    @app.post("/file/content")
    async def write_content(request: Request, path: str, overwrite: bool = False) -> JsonObject:
        async with runner.file_slot():
            await runner.quota.check()
            runner.workspace.path(path)
            content = bytearray()
            async for chunk in request.stream():
                if len(content) + len(chunk) > runner.settings.transfer_max_bytes:
                    raise HTTPException(413, "传入文件超过已配置的单次传输预算")
                content.extend(chunk)
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未写入")
            await asyncio.to_thread(runner.workspace.make_parents, path)
            if await request.is_disconnected():
                raise HTTPException(499, "调用方已断开，文件未写入")
            await asyncio.to_thread(
                runner.workspace.write, path, bytes(content), overwrite=overwrite
            )
            return {"path": path, "bytes": len(content)}

    @app.post("/cleanup")
    async def cleanup(request: CleanupRequest) -> JsonObject:
        if not request.confirmed:
            raise HTTPException(400, "清理前必须确认影响范围")
        runner.ensure_idle()
        runner.cleaning = True
        try:
            if runner.is_busy or runner.waiting or runner.active_file_ops > 0:
                raise HTTPException(409, "共享工作区正在执行或处理文件，请先停止执行")
            removed = await asyncio.to_thread(runner.workspace.cleanup, request.mode, request.paths)
        finally:
            runner.cleaning = False
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
