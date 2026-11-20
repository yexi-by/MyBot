"""工作区受控本地命令执行与并发文件调度服务。"""

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from pydantic import Field

from ..contracts import JSON_OBJECT, JsonObject, Model
from ..settings import WorkspaceSettings
from .storage import CleanupMode, Workspace, WorkspaceUnavailable


class WorkspaceBusyError(RuntimeError):
    """工作区正忙或正在清理，暂时拒绝互斥操作。"""


class WorkspaceOverloadedError(RuntimeError):
    """工作区排队或并发文件操作已满。"""


class ExecuteRequest(Model):
    command: str = Field(min_length=1, max_length=65_536)
    cwd: str = ""
    timeout: float | None = Field(default=None, gt=0, le=86_400)
    owner: str = ""


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


class RunningProcess(Protocol):
    def output(self) -> AsyncIterator[bytes]: ...
    async def wait(self) -> int: ...
    async def terminate(self) -> None: ...


class ProcessSpawner(Protocol):
    async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess: ...


def build_clean_env(ws_root: Path, exec_tmp: Path) -> dict[str, str]:
    home = ws_root / ".home"
    cache = ws_root / ".cache"
    venv_dir = ws_root / ".venv"
    path_parts: list[str] = []
    if os.name == "nt":
        candidates_nt = [
            *(([str(venv_dir / "Scripts")]) if (venv_dir / "Scripts").is_dir() else []),
            str(Path(sys.executable).parent),
            str(Path(sys.executable).resolve().parent),
        ]
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidates_nt.extend([rf"{sys_root}\System32", sys_root])
        for item in candidates_nt:
            if item not in path_parts:
                path_parts.append(item)
    else:
        candidates = [
            *(([str(venv_dir / "bin")]) if (venv_dir / "bin").is_dir() else []),
            str(home / ".local/bin"),
            str(home / ".npm/bin"),
            str(Path(sys.executable).resolve().parent),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        for item in candidates:
            if item not in path_parts:
                path_parts.append(item)

    env: dict[str, str] = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CACHE_HOME": str(cache),
        "UV_CACHE_DIR": str(cache / "uv"),
        "UV_PYTHON_DOWNLOADS": "never",
        "MPLCONFIGDIR": str(cache / "matplotlib"),
        "npm_config_cache": str(cache / "npm"),
        "npm_config_prefix": str(home / ".npm"),
        "TMPDIR": str(exec_tmp),
        "TMP": str(exec_tmp),
        "TEMP": str(exec_tmp),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
        "PATH": os.pathsep.join(path_parts),
    }
    if venv_dir.is_dir():
        env["VIRTUAL_ENV"] = str(venv_dir)
    if os.name == "nt":
        for key in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT"):
            if val := os.environ.get(key):
                env[key] = val
    for proxy_key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ):
        if val := os.environ.get(proxy_key):
            env[proxy_key] = val
    return env


class _LocalProcess:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    async def output(self) -> AsyncIterator[bytes]:
        stream = self.proc.stdout
        if stream is None:
            return
        while True:
            chunk = await stream.read(16_384)
            if not chunk:
                break
            yield chunk

    async def wait(self) -> int:
        return await self.proc.wait()

    async def terminate(self) -> None:
        if self.proc.returncode is not None:
            return
        pid = self.proc.pid
        if os.name == "nt":
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError, PermissionError:
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=3.0)
        except TimeoutError:
            pass


class LocalSpawner:
    async def spawn(self, command: str, cwd: Path, env: dict[str, str]) -> RunningProcess:
        if os.name == "nt":
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                command,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        return _LocalProcess(proc)


@dataclass
class _ActiveExecution:
    execution_id: str
    owner: str
    cancel_event: asyncio.Event
    process: RunningProcess | None = None


class WorkspaceService:
    def __init__(
        self,
        get_settings: Callable[[], WorkspaceSettings],
        *,
        config_root: Path | None = None,
        spawner: ProcessSpawner | None = None,
    ) -> None:
        self.get_settings = get_settings
        self.config_root = config_root.resolve() if config_root is not None else None
        self.spawner: ProcessSpawner = spawner or LocalSpawner()
        initial = get_settings()
        self.startup_root = initial.root
        self.startup_concurrency = max(1, initial.concurrent_executions)
        self.semaphore = asyncio.Semaphore(self.startup_concurrency)
        self.waiting: dict[str, tuple[str | None, asyncio.Event]] = {}
        self.active_slots: set[str] = set()
        self.active: dict[str, _ActiveExecution] = {}
        self.active_file_ops = 0
        self.cleaning = False
        self._workspace: Workspace | None = None
        self._prepared_root: Path | None = None

    def _resolve_root(self, raw_root: str) -> Path:
        candidate = Path(raw_root.strip() or "./workspace")
        if not candidate.is_absolute() and self.config_root is not None:
            candidate = self.config_root.parent / candidate
        return candidate.resolve()

    def workspace(self) -> Workspace:
        settings = self.get_settings()
        resolved = self._resolve_root(settings.root)
        if self._workspace is None or self._workspace.root != resolved:
            ws = Workspace(resolved)
            ws.ensure_distinct_from(self.config_root)
            self._workspace = ws
        else:
            self._workspace.ensure_distinct_from(self.config_root)
        return self._workspace

    def prepare_sync(self) -> None:
        settings = self.get_settings()
        if not settings.enabled:
            return
        ws = self.workspace()
        if self._prepared_root != ws.root:
            ws.prepare(clean_stale_exec_tmp=True)
            self._prepared_root = ws.root

    @property
    def execution_id(self) -> str | None:
        first = next(iter(self.active.values()), None)
        return first.execution_id if first is not None else None

    @property
    def is_busy(self) -> bool:
        return bool(self.active_slots or self.active)

    @property
    def restart_required(self) -> bool:
        current = self.get_settings()
        return (self.startup_root, self.startup_concurrency) != (
            current.root,
            max(1, current.concurrent_executions),
        )

    async def status(self) -> JsonObject:
        settings = self.get_settings()
        applied = JSON_OBJECT.validate_python(settings.model_dump(mode="json"))
        if not settings.enabled:
            return {
                "enabled": False,
                "available": False,
                "reason": "尚未启用共享工作区",
                "root": settings.root,
                "max_bytes": settings.max_bytes,
                "used_bytes": None,
                "busy": self.is_busy,
                "active_count": len(self.active),
                "execution_id": self.execution_id,
                "queued": len(self.waiting),
                "restart_required": self.restart_required,
                "applied_settings": applied,
            }
        try:
            ws = await asyncio.to_thread(self._prepared_workspace)
            used = await asyncio.to_thread(ws.used_bytes)
            over = used > settings.max_bytes
            return {
                "enabled": True,
                "available": True,
                "over_limit": over,
                "reason": "已超出空间预算上限，新写入与命令执行已暂停，请清理文件" if over else "",
                "root": str(ws.root),
                "max_bytes": settings.max_bytes,
                "used_bytes": used,
                "busy": self.is_busy,
                "active_count": len(self.active),
                "execution_id": self.execution_id,
                "queued": len(self.waiting),
                "restart_required": self.restart_required,
                "applied_settings": applied,
            }
        except (WorkspaceUnavailable, OSError, ValueError) as error:
            return {
                "enabled": True,
                "available": False,
                "reason": str(error)
                if isinstance(error, WorkspaceUnavailable)
                else f"工作区目录不可用（{type(error).__name__}）",
                "root": settings.root,
                "max_bytes": settings.max_bytes,
                "used_bytes": None,
                "busy": self.is_busy,
                "active_count": len(self.active),
                "execution_id": self.execution_id,
                "queued": len(self.waiting),
                "restart_required": self.restart_required,
                "applied_settings": applied,
            }

    def _prepared_workspace(self) -> Workspace:
        ws = self.workspace()
        if self._prepared_root != ws.root or not ws.root.exists():
            ws.prepare(clean_stale_exec_tmp=self._prepared_root is None)
            self._prepared_root = ws.root
        return ws

    def ensure_idle(self) -> None:
        if self.is_busy or self.waiting or self.active_file_ops > 0 or self.cleaning:
            raise WorkspaceBusyError("共享工作区正在执行命令或处理文件，请先停止执行")

    @asynccontextmanager
    async def file_slot(self) -> AsyncIterator[Workspace]:
        if self.cleaning:
            raise WorkspaceBusyError("共享工作区正在清理文件，请稍后重试")
        settings = self.get_settings()
        if self.active_file_ops >= settings.max_pending_operations:
            raise WorkspaceOverloadedError("工作区并发文件操作已满，请稍后重试")
        self.active_file_ops += 1
        try:
            ws = await asyncio.to_thread(self._prepared_workspace)
            yield ws
        finally:
            self.active_file_ops -= 1

    @asynccontextmanager
    async def operation_slot(
        self, owner: str | None
    ) -> AsyncIterator[tuple[str, asyncio.Event] | None]:
        if self.cleaning:
            raise WorkspaceBusyError("共享工作区正在清理文件，请稍后重试")
        settings = self.get_settings()
        if len(self.waiting) >= settings.max_pending_operations:
            raise WorkspaceOverloadedError(
                "工作区等待队列已满，请稍后重试或调整 max_pending_operations"
            )
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
                    raise WorkspaceBusyError("共享工作区正在清理文件，请稍后重试")
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
        return {"cancelled": running or matched > 0, "queued_cancelled": queued}

    async def close(self) -> None:
        await self.cancel()
        for item in list(self.active.values()):
            if item.process is not None:
                await item.process.terminate()

    async def files(self, path: str = "", offset: int = 0, limit: int = 100) -> JsonObject:
        async with self.file_slot() as ws:
            return await asyncio.to_thread(ws.files, path, offset, limit)

    async def search(
        self,
        query: str = "",
        *,
        path: str = "",
        glob: str = "*",
        regex: bool = False,
        case_sensitive: bool = False,
        include_hidden: bool = False,
        max_results: int = 100,
    ) -> JsonObject:
        async with self.file_slot() as ws:
            return await asyncio.to_thread(
                ws.search,
                query,
                relative=path,
                glob_pattern=glob,
                regex=regex,
                case_sensitive=case_sensitive,
                include_hidden=include_hidden,
                max_results=max_results,
            )

    async def read_text(self, path: str, offset: int = 0, limit: int = 65_536) -> JsonObject:
        settings = self.get_settings()
        capped = min(limit, settings.transfer_max_bytes)
        async with self.file_slot() as ws:
            return await asyncio.to_thread(ws.read_text, path, offset, capped)

    async def read_file(self, path: str) -> bytes:
        settings = self.get_settings()
        async with self.file_slot() as ws:
            return await asyncio.to_thread(ws.read, path, settings.transfer_max_bytes)

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = False,
        append: bool = False,
    ) -> JsonObject:
        settings = self.get_settings()
        encoded = content.encode("utf-8")
        if len(encoded) > settings.transfer_max_bytes:
            raise ValueError("写入内容超过单次文件传输预算")
        async with self.file_slot() as ws:
            await asyncio.to_thread(ws.check_capacity, settings.max_bytes, len(encoded))
            await asyncio.to_thread(ws.write, path, encoded, overwrite=overwrite, append=append)
            return {"path": path, "bytes": len(encoded)}

    async def write_bytes(self, path: str, content: bytes, *, overwrite: bool = False) -> None:
        settings = self.get_settings()
        if len(content) > settings.transfer_max_bytes:
            raise ValueError("写入文件超过单次传输预算")
        async with self.file_slot() as ws:
            await asyncio.to_thread(ws.check_capacity, settings.max_bytes, len(content))
            await asyncio.to_thread(ws.write, path, content, overwrite=overwrite)

    async def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> JsonObject:
        settings = self.get_settings()
        async with self.file_slot() as ws:
            await asyncio.to_thread(
                ws.check_capacity,
                settings.max_bytes,
                max(0, len(new_string.encode("utf-8")) - len(old_string.encode("utf-8"))),
            )
            return await asyncio.to_thread(
                ws.edit_text,
                path,
                old_string,
                new_string,
                replace_all=replace_all,
                max_bytes=settings.transfer_max_bytes,
            )

    async def delete_path(self, path: str, *, recursive: bool = False) -> JsonObject:
        async with self.file_slot() as ws:
            return await asyncio.to_thread(ws.delete, path, recursive=recursive)

    async def path_exists(self, path: str) -> bool:
        async with self.file_slot() as ws:
            return await asyncio.to_thread(lambda: ws.path(path).exists())

    async def cleanup(
        self, mode: CleanupMode, paths: list[str], confirmed: bool = False
    ) -> JsonObject:
        if not confirmed:
            raise ValueError("清理前必须确认影响范围")
        self.ensure_idle()
        self.cleaning = True
        try:
            if self.is_busy or self.waiting or self.active_file_ops > 0:
                raise WorkspaceBusyError("共享工作区正在执行命令或处理文件，请先停止执行")
            ws = await asyncio.to_thread(self._prepared_workspace)
            removed = await asyncio.to_thread(ws.cleanup, mode, paths)
        finally:
            self.cleaning = False
        return JSON_OBJECT.validate_python({"removed": removed, **await self.status()})

    async def execute(self, request: ExecuteRequest) -> JsonObject:
        async with self.operation_slot(request.owner) as slot:
            if slot is None:
                return {"status": "cancelled", "exit_code": None, "output": "", "truncated": False}
            slot_id, cancel_event = slot
            settings = self.get_settings()
            if not settings.enabled:
                raise WorkspaceUnavailable("尚未启用共享工作区")
            ws = await asyncio.to_thread(self._prepared_workspace)
            await asyncio.to_thread(ws.check_capacity, settings.max_bytes)
            if cancel_event.is_set():
                return {"status": "cancelled", "exit_code": None, "output": "", "truncated": False}
            cwd = ws.path(request.cwd, allow_root=True)
            if not cwd.is_dir():
                raise ValueError("执行目录不存在")

            execution_id = uuid4().hex
            active = _ActiveExecution(
                execution_id=execution_id,
                owner=request.owner,
                cancel_event=cancel_event,
            )
            self.active[slot_id] = active
            retainer = HeadTailRetainer(settings.output_limit)
            spilled = False
            output_complete = False
            output_path = f".outputs/{execution_id}.log"
            output_file: BinaryIO | None = None
            status, exit_code = "ok", -1
            timeout = min(request.timeout or settings.command_timeout, settings.command_timeout)
            deadline = asyncio.get_running_loop().time() + timeout
            exec_tmp = await asyncio.to_thread(ws.create_exec_tmp, execution_id)

            try:

                async def drain_logs(proc: RunningProcess) -> None:
                    nonlocal output_file, spilled, output_complete
                    async for chunk in proc.output():
                        if not chunk:
                            continue
                        if not spilled and retainer.total + len(chunk) > retainer.limit:
                            prefix = bytes(retainer.head)

                            def _open_and_seed(seed: bytes, extra: bytes) -> BinaryIO:
                                fh = ws.open_exclusive_binary(output_path)
                                if seed:
                                    fh.write(seed)
                                fh.write(extra)
                                return fh

                            output_file = await asyncio.to_thread(_open_and_seed, prefix, chunk)
                            spilled = True
                        elif spilled and output_file is not None:
                            await asyncio.to_thread(output_file.write, chunk)
                        retainer.push(chunk)
                    output_complete = True

                env = build_clean_env(ws.root, exec_tmp)
                proc = await self.spawner.spawn(request.command, cwd, env)
                active.process = proc
                log_task = asyncio.create_task(drain_logs(proc))
                wait_task = asyncio.create_task(proc.wait())
                cancel_task = asyncio.create_task(cancel_event.wait())
                try:
                    async with asyncio.timeout_at(deadline):
                        done, _ = await asyncio.wait(
                            {wait_task, cancel_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if cancel_task in done:
                            status = "cancelled"
                            await proc.terminate()
                        else:
                            exit_code = await wait_task
                            if exit_code != 0:
                                status = "error"
                except TimeoutError:
                    status = "timeout"
                    await proc.terminate()
                finally:
                    cancel_task.cancel()
                    wait_task.cancel()
                    await asyncio.gather(wait_task, cancel_task, return_exceptions=True)

                if status == "ok" or status == "error":
                    remaining = max(0.05, deadline - asyncio.get_running_loop().time())
                    cancel_drain = asyncio.create_task(cancel_event.wait())
                    try:
                        async with asyncio.timeout(remaining):
                            done_drain, _ = await asyncio.wait(
                                {log_task, cancel_drain},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if cancel_drain in done_drain and not log_task.done():
                                status = "cancelled"
                                await proc.terminate()
                                log_task.cancel()
                            else:
                                await log_task
                    except TimeoutError:
                        log_task.cancel()
                    finally:
                        cancel_drain.cancel()
                        await asyncio.gather(log_task, cancel_drain, return_exceptions=True)
                else:
                    log_task.cancel()
                    await asyncio.gather(log_task, return_exceptions=True)
            except asyncio.CancelledError:
                if active.process is not None:
                    await active.process.terminate()
                raise
            finally:
                if output_file is not None:
                    await asyncio.to_thread(output_file.close)
                if active.process is not None:
                    await active.process.terminate()
                self.active.pop(slot_id, None)
                await asyncio.to_thread(ws.remove_exec_tmp, execution_id)
                if spilled:
                    await asyncio.to_thread(ws.prune_outputs)
                ws.invalidate_usage()

            return {
                "status": status,
                "exit_code": exit_code if status in ("ok", "error") else None,
                "output": retainer.render(output_path),
                "truncated": retainer.truncated,
                "output_complete": output_complete,
                "output_path": output_path if spilled else None,
            }
