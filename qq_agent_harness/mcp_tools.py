"""MCP 接入；远端注解不赋予权限，所有调用经过统一目录。"""

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import timedelta

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from mcp.types import CONNECTION_CLOSED, CallToolResult

from .contracts import (
    JSON_OBJECT,
    JsonObject,
    JsonValue,
    ToolContext,
    ToolDefinition,
    ToolResult,
    json_binding,
)
from .registry import ToolRegistry
from .settings import MCPSettings

RETRY_INITIAL = 1.0
RETRY_MAX = 30.0
REFRESH_INTERVAL = 30.0


class MCPManager:
    def __init__(self, configs: dict[str, MCPSettings], registry: ToolRegistry) -> None:
        self.configs = configs
        self.registry = registry
        self.tasks: list[asyncio.Task[None]] = []
        self.statuses: dict[str, JsonObject] = {}
        self.directories: dict[str, dict[str, ToolDefinition]] = {}
        self.connections: dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        for name, config in self.configs.items():
            if config.enabled:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    raise ValueError("MCP 服务名称只能使用字母、数字、下划线或连字符")
                self.statuses[name] = {"state": "connecting"}
                self.tasks.append(asyncio.create_task(self._serve(name, config)))

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _serve(self, name: str, config: MCPSettings) -> None:
        retry_delay = RETRY_INITIAL
        while True:
            disconnected = asyncio.Event()
            self.connections[name] = disconnected
            self.statuses[name] = {"state": "connecting"}
            connected_at: float | None = None

            def disconnect(event: asyncio.Event = disconnected) -> None:
                # 旧连接中尚未返回的调用不能撤掉重连后的新目录。
                event.set()
                if self.connections.get(name) is event:
                    self._withdraw(name)
                    self.statuses[name] = {
                        "state": "reconnecting",
                        "message": "MCP 连接中断，已撤下工具；等待重新连接",
                    }

            try:
                async with AsyncExitStack() as stack:
                    if config.transport == "stdio":
                        # 子进程输出可能含凭据、聊天或工具正文，不转储到主服务日志。
                        errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                        read, write = await stack.enter_async_context(
                            stdio_client(
                                StdioServerParameters(
                                    command=config.command, args=config.args, env=config.env
                                ),
                                errlog=errlog,
                            )
                        )
                    else:
                        client = await stack.enter_async_context(
                            httpx.AsyncClient(
                                headers=config.headers, timeout=config.timeout, trust_env=False
                            )
                        )
                        read, write, _ = await stack.enter_async_context(
                            streamable_http_client(config.url, http_client=client)
                        )
                    observed_write, observed_read = anyio.create_memory_object_stream[
                        SessionMessage | Exception
                    ](16)
                    await stack.enter_async_context(observed_write)
                    await stack.enter_async_context(observed_read)
                    relay = asyncio.create_task(self._relay(read, observed_write, disconnect))
                    session = await stack.enter_async_context(
                        ClientSession(
                            observed_read,
                            write,
                            read_timeout_seconds=timedelta(seconds=config.timeout),
                        )
                    )
                    stack.push_async_callback(self._stop_relay, relay)
                    await session.initialize()
                    await self._refresh(name, config, session, disconnected, disconnect)
                    if disconnected.is_set():
                        raise ConnectionError("MCP connection closed")
                    connected_at = time.monotonic()
                    self.statuses[name] = {**self.statuses[name], "state": "connected"}
                    # EOF 立即唤醒；目录的定期刷新不承担断线探测职责。
                    while True:
                        try:
                            await asyncio.wait_for(disconnected.wait(), REFRESH_INTERVAL)
                        except TimeoutError:
                            try:
                                await self._refresh(name, config, session, disconnected, disconnect)
                            except McpError, ValueError:
                                self.statuses[name] = {
                                    **self.statuses[name],
                                    "state": "connected",
                                    "message": "工具目录刷新失败，保留上次可用目录；稍后重新刷新",
                                }
                            continue
                        raise ConnectionError("MCP connection closed")
            except asyncio.CancelledError:
                self.statuses[name] = {"state": "stopped"}
                raise
            except Exception as error:
                # 连接稳定运行过后重置退避；快速反复掉线继续增加间隔。
                if connected_at is not None and time.monotonic() - connected_at >= REFRESH_INTERVAL:
                    retry_delay = RETRY_INITIAL
                self.statuses[name] = {
                    "state": "reconnecting",
                    "message": "MCP 连接或工具目录不可用，将自动重新连接；不会重放工具调用",
                    "error_type": type(error).__name__,
                    "retry_in": retry_delay,
                }
            finally:
                self._withdraw(name)
                if self.connections.get(name) is disconnected:
                    del self.connections[name]
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, RETRY_MAX)

    @staticmethod
    async def _relay(
        source: MemoryObjectReceiveStream[SessionMessage | Exception],
        destination: MemoryObjectSendStream[SessionMessage | Exception],
        disconnect: Callable[[], None],
    ) -> None:
        try:
            async for message in source:
                if isinstance(message, Exception):
                    disconnect()
                    return
                await destination.send(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            disconnect()
            await destination.aclose()

    @staticmethod
    async def _stop_relay(task: asyncio.Task[None]) -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _withdraw(self, name: str) -> None:
        # 精确移除自己登记的对象，服务名互为前缀时也不会删除别人的工具。
        for tool_name, definition in self.directories.pop(name, {}).items():
            if self.registry.tools.get(tool_name) is definition:
                del self.registry.tools[tool_name]

    async def _refresh(
        self,
        name: str,
        config: MCPSettings,
        session: ClientSession,
        disconnected: asyncio.Event,
        disconnect: Callable[[], None],
    ) -> None:
        cursor: str | None = None
        definitions: list[ToolDefinition] = []
        seen_cursors: set[str] = set()
        excluded: list[JsonValue] = []
        while True:
            page = await session.list_tools(cursor)
            for item in page.tools:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", item.name):
                    excluded.append({"name": item.name, "reason": "工具名称不符合调用协议"})
                    continue
                try:
                    parameters = JSON_OBJECT.validate_python(item.inputSchema)
                    Draft202012Validator.check_schema(parameters)
                except ValueError, SchemaError:
                    excluded.append({"name": item.name, "reason": "工具参数 schema 无效"})
                    continue
                # 禁止 JSON schema 的远程引用触发额外网络读取。
                if any(
                    isinstance(ref, str) and not ref.startswith("#")
                    for ref in find_refs(parameters)
                ):
                    excluded.append({"name": item.name, "reason": "工具 schema 含外部引用"})
                    continue
                # 注解只描述执行特征，不授予调用权限；本地声明可覆盖服务提供的特征。
                effects = item.name in config.effect_tools or not (
                    item.name in config.read_only_tools
                    or (item.annotations is not None and item.annotations.readOnlyHint is True)
                )
                # 远端标题只用于控制台展示，不参与授权判断。
                title = item.title or (item.annotations.title if item.annotations else None)
                definitions.append(
                    ToolDefinition(
                        f"mcp__{name}__{item.name}",
                        item.description
                        or f"当你需要 {item.name} 能力辅助当前对话时，可主动调用。",
                        f"mcp.{name}",
                        json_binding(
                            parameters,
                            self._handler(
                                session,
                                item.name,
                                config.timeout,
                                effects,
                                disconnected,
                                disconnect,
                            ),
                        ),
                        effects=effects,
                        title=title or item.name,
                    )
                )
            cursor = page.nextCursor
            if cursor is None:
                break
            if cursor in seen_cursors:
                raise ValueError("MCP 工具分页游标循环")
            seen_cursors.add(cursor)
        if disconnected.is_set():
            raise ConnectionError("MCP connection closed")
        self._withdraw(name)
        owned: dict[str, ToolDefinition] = {}
        self.directories[name] = owned
        for definition in definitions:
            if definition.name in owned:
                excluded.append({"name": definition.name, "reason": "工具名称重复"})
                continue
            self.registry.add(definition)
            owned[definition.name] = definition
        self.statuses[name] = {**self.statuses.get(name, {}), "excluded_tools": excluded}

    @staticmethod
    def _handler(
        session: ClientSession,
        name: str,
        timeout: float,
        effects: bool,
        disconnected: asyncio.Event,
        disconnect: Callable[[], None],
    ):
        async def call(_context: ToolContext, arguments: JsonObject) -> ToolResult:
            if disconnected.is_set():
                return ToolResult.failure("MCP 连接已断开，调用未提交", "MCPDisconnected")
            operation: asyncio.Task[CallToolResult] | None = None
            connection_lost: asyncio.Task[bool] | None = None
            try:
                if denied := _context.recheck():
                    return denied
                _context.effect_submitted = effects
                operation = asyncio.create_task(
                    session.call_tool(
                        name, arguments, read_timeout_seconds=timedelta(seconds=timeout)
                    )
                )
                connection_lost = asyncio.create_task(disconnected.wait())
                done, _ = await asyncio.wait(
                    (operation, connection_lost), return_when=asyncio.FIRST_COMPLETED
                )
                if operation not in done:
                    raise ConnectionError("MCP connection closed before response")
                response = await operation
                raw = JSON_OBJECT.validate_python(
                    response.model_dump(mode="json", exclude_none=True)
                )
                media: list[JsonValue] = []
                content = raw.get("content")
                if isinstance(content, list):
                    for item in content:
                        if (
                            isinstance(item, dict)
                            and item.get("type") in ("image", "audio")
                            and isinstance(item.get("data"), str)
                        ):
                            media.append(
                                {
                                    "type": item["type"],
                                    "mime_type": item.get("mimeType", ""),
                                    "base64": item.pop("data"),
                                }
                            )
                raw["media"] = media
                return ToolResult(
                    status="error" if response.isError else "ok",
                    data=raw,
                    message="MCP 工具返回错误" if response.isError else "",
                )
            except asyncio.CancelledError:
                raise
            except McpError as error:
                # -32000 也可能是服务端普通错误；是否断线由接收通道确认。
                if error.error.code in (CONNECTION_CLOSED, 408, -32001):
                    return ToolResult(
                        status="unknown" if effects else "error",
                        message=f"MCP 未取得回执（JSON-RPC {error.error.code}）",
                        error_type="MCPTransportError",
                    )
                return ToolResult.failure(
                    f"MCP 调用被拒绝（JSON-RPC {error.error.code}）；请检查工具参数",
                    "MCPRemoteError",
                )
            except TimeoutError, httpx.TimeoutException:
                return ToolResult(
                    status="unknown" if effects else "error",
                    message="MCP 单次调用超时；连接与其他工具保留",
                    error_type="MCPTimeout",
                )
            except (
                ConnectionError,
                OSError,
                httpx.TransportError,
                anyio.EndOfStream,
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
            ):
                disconnect()
                return ToolResult(
                    status="unknown" if effects else "error",
                    message="MCP 连接已中断；已提交动作的状态以实际回执为准",
                    error_type="MCPError",
                )
            except Exception as error:
                return ToolResult(
                    status="unknown" if effects else "error",
                    message=f"MCP 单次调用失败（{type(error).__name__}）；其他工具仍可调用",
                    error_type=type(error).__name__,
                )
            finally:
                pending = [task for task in (operation, connection_lost) if task is not None]
                for task in pending:
                    if not task.done():
                        task.cancel()
                waiting: list[Awaitable[object]] = [task for task in pending]
                await asyncio.gather(*waiting, return_exceptions=True)

        return call


def find_refs(value: JsonValue) -> list[JsonValue]:
    if isinstance(value, dict):
        return [value[key] for key in ("$ref", "$dynamicRef") if key in value] + [
            ref for child in value.values() for ref in find_refs(child)
        ]
    if isinstance(value, list):
        return [ref for child in value for ref in find_refs(child)]
    return []
