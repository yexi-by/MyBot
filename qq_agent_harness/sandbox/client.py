"""Harness 与可信 runner 的客户端；不向工具结果回显连接密钥。"""

import asyncio
from collections.abc import Callable
from typing import Literal

import httpx

from ..contracts import JSON_OBJECT, JsonObject
from ..settings import SandboxSettings

CleanupMode = Literal["temp", "cache", "selected", "all"]


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

    async def search_files(
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
        return JSON_OBJECT.validate_python(
            (
                await self.request(
                    "GET",
                    "/files/search",
                    params={
                        "query": query,
                        "path": path,
                        "glob": glob,
                        "regex": "true" if regex else "false",
                        "case_sensitive": "true" if case_sensitive else "false",
                        "include_hidden": "true" if include_hidden else "false",
                        "max_results": str(max_results),
                    },
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

    async def cleanup(self, mode: CleanupMode, paths: list[str], confirmed: bool) -> JsonObject:
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

    async def write_bytes(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        settings = self.get_settings()
        if not settings.enabled or not settings.runner_token:
            raise ValueError("共享沙箱未启用或缺少连接凭据")
        if len(data) > settings.transfer_max_bytes:
            raise ValueError(
                f"传入文件超过已配置的单次传输上限（{settings.transfer_max_bytes} 字节）"
            )
        params = {"path": path}
        if overwrite:
            params["overwrite"] = "true"
        response = await self.http.post(
            settings.runner_url.rstrip("/") + "/file/content",
            params=params,
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

    async def edit_file(
        self, path: str, old_string: str, new_string: str, *, replace_all: bool = False
    ) -> JsonObject:
        return JSON_OBJECT.validate_python(
            (
                await self.request(
                    "POST",
                    "/file/edit",
                    body={
                        "path": path,
                        "old_string": old_string,
                        "new_string": new_string,
                        "replace_all": replace_all,
                    },
                )
            ).json()
        )

    async def delete_path(self, path: str, *, recursive: bool = False) -> JsonObject:
        return JSON_OBJECT.validate_python(
            (
                await self.request(
                    "POST",
                    "/file/delete",
                    body={"path": path, "recursive": recursive},
                )
            ).json()
        )

    async def execute(
        self, command: str, cwd: str = "", owner: str = "", *, timeout: float | None = None
    ) -> JsonObject:
        body: JsonObject = {"command": command, "cwd": cwd, "owner": owner}
        if timeout is not None:
            body["timeout"] = timeout
        try:
            return JSON_OBJECT.validate_python(
                (await self.request("POST", "/exec", body=body)).json()
            )
        except asyncio.CancelledError, httpx.TimeoutException:
            try:
                await asyncio.shield(self.cancel(owner))
            except httpx.HTTPError, ValueError:
                pass
            raise

    async def close(self) -> None:
        await self.http.aclose()
