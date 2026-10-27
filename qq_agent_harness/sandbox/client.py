"""Harness 与可信 runner 的客户端；不向工具结果回显连接密钥。"""

import asyncio
from collections.abc import Callable
from typing import Literal

import httpx

from ..contracts import JSON_OBJECT, JsonObject
from ..settings import SandboxSettings


class RunnerClient:
    def __init__(
        self,
        get_settings: Callable[[], SandboxSettings],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.get_settings = get_settings
        self.http = httpx.AsyncClient(transport=transport, trust_env=False)

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: JsonObject | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        settings = self.get_settings()
        if not settings.enabled:
            raise ValueError("此部署尚未启用共享沙箱")
        if not settings.runner_token:
            raise ValueError("共享沙箱缺少 runner 连接凭据")
        response = await self.http.request(
            method,
            settings.runner_url.rstrip("/") + path,
            json=body,
            params=params,
            headers={"Authorization": f"Bearer {settings.runner_token}"},
            timeout=httpx.Timeout(connect=30, read=None, write=30, pool=30),
        )
        response.raise_for_status()
        return response

    async def status(self) -> JsonObject:
        if not self.get_settings().enabled:
            return {
                "available": False,
                "reason": "共享沙箱未启用",
                "busy": False,
                "quota_bytes": self.get_settings().quota_bytes,
                "used_bytes": None,
                "execution_id": None,
            }
        return JSON_OBJECT.validate_python((await self.request("GET", "/status")).json())

    async def files(self, path: str = "", offset: int = 0, limit: int = 100) -> JsonObject:
        return JSON_OBJECT.validate_python(
            (
                await self.request(
                    "GET",
                    "/files",
                    params={"path": path, "offset": str(offset), "limit": str(limit)},
                )
            ).json()
        )

    async def read_text(self, path: str, offset: int = 0, limit: int = 65_536) -> JsonObject:
        return JSON_OBJECT.validate_python(
            (
                await self.request(
                    "GET",
                    "/file/text",
                    params={"path": path, "offset": str(offset), "limit": str(limit)},
                )
            ).json()
        )

    async def cleanup(
        self, mode: Literal["cache", "selected", "all"], paths: list[str], confirmed: bool
    ) -> JsonObject:
        return JSON_OBJECT.validate_python(
            (
                await self.request(
                    "POST",
                    "/cleanup",
                    body=JSON_OBJECT.validate_python(
                        {"mode": mode, "paths": paths, "confirmed": confirmed}
                    ),
                )
            ).json()
        )

    async def cancel(self, owner: str = "") -> JsonObject:
        return JSON_OBJECT.validate_python(
            (await self.request("POST", "/cancel", body={"owner": owner})).json()
        )

    async def read_file(self, path: str) -> bytes:
        return (await self.request("GET", "/file", params={"path": path})).content

    async def write_bytes(self, path: str, data: bytes) -> None:
        settings = self.get_settings()
        if not settings.enabled or not settings.runner_token:
            raise ValueError("共享沙箱未启用或缺少连接凭据")
        if len(data) > settings.transfer_max_bytes:
            raise ValueError(
                f"传入文件超过已配置的单次传输上限（{settings.transfer_max_bytes} 字节）"
            )
        response = await self.http.post(
            settings.runner_url.rstrip("/") + "/file/content",
            params={"path": path},
            content=data,
            headers={
                "Authorization": f"Bearer {settings.runner_token}",
                "Content-Type": "application/octet-stream",
            },
            timeout=settings.command_timeout + 30,
        )
        response.raise_for_status()

    async def write_file(
        self, path: str, content: str, overwrite: bool = False, append: bool = False
    ) -> JsonObject:
        return JSON_OBJECT.validate_python(
            (
                await self.request(
                    "PUT",
                    "/file",
                    body={
                        "path": path,
                        "content": content,
                        "overwrite": overwrite,
                        "append": append,
                    },
                )
            ).json()
        )

    async def execute(self, command: str, cwd: str = "", owner: str = "") -> JsonObject:
        try:
            return JSON_OBJECT.validate_python(
                (
                    await self.request(
                        "POST", "/exec", body={"command": command, "cwd": cwd, "owner": owner}
                    )
                ).json()
            )
        except asyncio.CancelledError, httpx.TimeoutException:
            try:
                await asyncio.shield(self.cancel(owner))
            except httpx.HTTPError, ValueError:
                pass
            raise

    async def close(self) -> None:
        await self.http.aclose()
