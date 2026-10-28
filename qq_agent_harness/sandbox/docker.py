"""仅可信 runner 可调用 Docker API；模型不能提交容器配置。"""

import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from ..contracts import JSON_OBJECT, JsonObject
from ..settings import SandboxSettings
from .quota import SandboxUnavailable


class Engine(Protocol):
    async def create(self, name: str, config: JsonObject) -> str: ...
    async def start(self, container_id: str) -> None: ...
    def logs(self, container_id: str) -> AsyncIterator[bytes]: ...
    async def wait(self, container_id: str) -> int: ...
    async def remove(self, container_id: str) -> None: ...
    async def prune_managed(self) -> None: ...
    async def close(self) -> None: ...


class DockerEngine:
    def __init__(self, workspace: str, socket: str = "/var/run/docker.sock"):
        self.workspace = workspace
        transport = httpx.AsyncHTTPTransport(uds=socket)
        self.http = httpx.AsyncClient(
            transport=transport, base_url="http://docker/v1.45", timeout=30
        )

    async def create(self, name: str, config: JsonObject) -> str:
        result = await self.http.post("/containers/create", params={"name": name}, json=config)
        result.raise_for_status()
        payload = JSON_OBJECT.validate_python(result.json())
        container_id = payload.get("Id")
        if not isinstance(container_id, str):
            raise SandboxUnavailable("Docker 未返回执行容器标识")
        return container_id

    async def network_info(self, name: str) -> JsonObject:
        result = await self.http.get(f"/networks/{name}")
        result.raise_for_status()
        return JSON_OBJECT.validate_python(result.json())

    async def start(self, container_id: str) -> None:
        result = await self.http.post(f"/containers/{container_id}/start")
        result.raise_for_status()

    async def logs(self, container_id: str) -> AsyncIterator[bytes]:
        async with self.http.stream(
            "GET",
            f"/containers/{container_id}/logs",
            params={"stdout": "1", "stderr": "1", "follow": "1"},
            timeout=None,
        ) as result:
            result.raise_for_status()
            pending = bytearray()
            async for block in result.aiter_bytes():
                pending.extend(block)
                while len(pending) >= 8:
                    size = int.from_bytes(pending[4:8], "big")
                    if len(pending) < size + 8:
                        break
                    yield bytes(pending[8 : size + 8])
                    del pending[: size + 8]

    async def wait(self, container_id: str) -> int:
        result = await self.http.post(f"/containers/{container_id}/wait", timeout=None)
        result.raise_for_status()
        value = JSON_OBJECT.validate_python(result.json()).get("StatusCode")
        return value if isinstance(value, int) else -1

    async def remove(self, container_id: str) -> None:
        result = await self.http.delete(
            f"/containers/{container_id}", params={"force": "1", "v": "1"}
        )
        if result.status_code != 404:
            result.raise_for_status()

    async def prune_managed(self) -> None:
        result = await self.http.get(
            "/containers/json",
            params={
                "all": "1",
                "filters": json.dumps(
                    {
                        "label": [
                            "qq-agent-harness.execution=true",
                            f"qq-agent-harness.workspace={self.workspace}",
                        ]
                    }
                ),
            },
        )
        result.raise_for_status()
        values: object = result.json()
        if not isinstance(values, list):
            raise SandboxUnavailable("Docker 容器列表格式错误")
        for item in values:
            data = JSON_OBJECT.validate_python(item)
            identifier = data.get("Id")
            if isinstance(identifier, str):
                await self.remove(identifier)

    async def close(self) -> None:
        await self.http.aclose()


def container_config(
    *,
    command: str,
    cwd: str,
    image: str,
    host_workspace: str,
    network: str,
    proxy: str,
    settings: SandboxSettings,
) -> JsonObject:
    return {
        "Image": image,
        "User": "1000:1000",
        "WorkingDir": "/workspace" + ("/" + cwd if cwd else ""),
        "Entrypoint": [
            "/usr/local/bin/python",
            "-I",
            "-S",
            "/usr/local/libexec/qah-sandbox-guard.py",
        ],
        "Cmd": ["/bin/bash", "--noprofile", "--norc", "-c", command],
        "Env": [
            "HOME=/workspace/.home",
            "XDG_CACHE_HOME=/workspace/.cache",
            "UV_CACHE_DIR=/workspace/.cache/uv",
            "UV_PYTHON_DOWNLOADS=never",
            "MPLCONFIGDIR=/workspace/.cache/matplotlib",
            "TMPDIR=/workspace/.tmp",
            "npm_config_cache=/workspace/.cache/npm",
            "npm_config_prefix=/workspace/.home/.npm",
            "NODE_USE_ENV_PROXY=1",
            "PYTHONUNBUFFERED=1",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONSAFEPATH=1",
            f"HTTP_PROXY={proxy}",
            f"HTTPS_PROXY={proxy}",
            f"http_proxy={proxy}",
            f"https_proxy={proxy}",
            "NO_PROXY=",
            "no_proxy=",
            "PATH=/workspace/.home/.local/bin:/workspace/.home/.npm/bin:/usr/local/bin:/usr/bin:/bin",
        ],
        "Labels": {
            "qq-agent-harness.execution": "true",
            "qq-agent-harness.workspace": host_workspace,
        },
        "AttachStdout": True,
        "AttachStderr": True,
        "OpenStdin": False,
        "Tty": False,
        "HostConfig": {
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Binds": [f"{host_workspace}:/workspace:rw"],
            "NetworkMode": network,
            "Dns": ["127.0.0.1"],
            "Memory": settings.memory_bytes,
            "MemorySwap": settings.memory_bytes,
            "NanoCpus": int(settings.cpus * 1_000_000_000),
            "PidsLimit": settings.pids_limit,
            "Ulimits": [{"Name": "core", "Soft": 0, "Hard": 0}],
            "ShmSize": 16_777_216,
            "Init": True,
            "RestartPolicy": {"Name": "no"},
            "LogConfig": {
                "Type": "local",
                "Config": {"max-size": "2m", "max-file": "1", "compress": "false"},
            },
        },
    }
