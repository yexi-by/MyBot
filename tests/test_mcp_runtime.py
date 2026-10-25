"""通过 stdio 子进程和真实 HTTP 连接验证 MCP 调用、超时与恢复。"""

import asyncio
import socket
import sys
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.config import MCPConfig, MCPServerConfig
from app.models import JsonObject
from app.services.llm.mcp import MCPToolManager


SERVER_SCRIPT = """
import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if 'id' not in request:
        continue
    if method == 'initialize':
        if sys.argv[1] == 'hang':
            continue
        result = {'protocolVersion': request['params']['protocolVersion'],
                  'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'test', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'inspect', 'inputSchema': {'type': 'object'}}]}
    elif method == 'tools/call':
        if request['params'].get('arguments', {}).get('wait'):
            continue
        result = {'isError': True, 'structuredContent': {'message': 'expected failure'}, 'content': []}
    else:
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
"""


def manager_for(mode: str) -> MCPToolManager:
    return MCPToolManager(MCPConfig(
        enabled=True, initialization_timeout_seconds=1, call_timeout_seconds=0.05,
        servers={"test": MCPServerConfig(
            command=sys.executable, args=("-u", "-c", SERVER_SCRIPT, mode), disabled=False,
        )},
    ))


@asynccontextmanager
async def http_mcp_server() -> AsyncIterator[str]:
    """运行要求 Bearer 认证的 MCP HTTP 服务，实际验证协议与参数传递。"""
    app = FastAPI()

    async def no_event_stream() -> Response:
        return Response(status_code=405)

    async def handle(request: Request) -> Response:
        if request.headers.get("authorization") != "Bearer test-api-key":
            return Response(status_code=401)
        body = cast(dict[str, object], await request.json())
        if "id" not in body:
            return Response(status_code=202)
        params = cast(dict[str, object], body.get("params", {}))
        if body["method"] == "initialize":
            result: dict[str, object] = {
                "protocolVersion": params["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-http", "version": "1"},
            }
        elif body["method"] == "tools/list":
            result = {"tools": [{
                "name": "inspect",
                "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
            }]}
        elif body["method"] == "tools/call":
            arguments = cast(dict[str, object], params.get("arguments", {}))
            if arguments.get("wait"):
                await asyncio.sleep(1)
            result = {"isError": False, "structuredContent": arguments, "content": []}
        else:
            return Response(status_code=400)
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": result})

    app.add_api_route("/mcp", no_event_stream, methods=["GET"])
    app.add_api_route("/mcp", handle, methods=["POST"])

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off", ws="none"))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(3):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("测试 HTTP 服务未启动")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            server.should_exit = True
            await task


class MCPRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_authentication_arguments_and_timeout_recovery(self) -> None:
        """HTTP 认证头、工具参数及超时后的后续调用走完整连接路径。"""
        async with http_mcp_server() as url:
            manager = MCPToolManager(MCPConfig.model_validate({
                "enabled": True,
                "initialization_timeout_seconds": 3,
                "call_timeout_seconds": 0.25,
                "servers": {"remote": {
                    "transport": "streamable_http", "url": url,
                    "headers": {"Authorization": "Bearer test-api-key"},
                    "disabled": False,
                }},
            }))
            try:
                await manager.start()
                tools = manager.list_tools()
                self.assertEqual([tool.name for tool in tools], ["mcp__remote__inspect"])
                self.assertIn("url", cast(JsonObject, tools[0].parameters["properties"]))
                timeout = cast(JsonObject, await manager.call_tool("mcp__remote__inspect", {"wait": True}))
                self.assertEqual(timeout["error_type"], "TimeoutError")
                result = await manager.call_tool("mcp__remote__inspect", {"url": "https://example.com"})
                self.assertEqual(result, {"is_error": False, "data": {"url": "https://example.com"}})
            finally:
                await manager.close()
            self.assertEqual(manager.list_tools(), [])

    async def test_initialization_timeout_cleans_up_and_allows_restart(self) -> None:
        manager = manager_for("hang")
        with self.assertRaisesRegex(TimeoutError, "test.*初始化超时"):
            await manager.start()
        self.assertEqual(manager.list_tools(), [])
        manager.config = manager_for("ready").config
        try:
            await manager.start()
            self.assertEqual([tool.name for tool in manager.list_tools()], ["mcp__test__inspect"])
        finally:
            await manager.close()

    async def test_call_timeout_recovers_and_preserves_structured_error(self) -> None:
        manager = manager_for("ready")
        try:
            await manager.start()
            timeout = cast(JsonObject, await manager.call_tool("mcp__test__inspect", {"wait": True}))
            self.assertIs(timeout["is_error"], True)
            self.assertEqual(timeout["error_type"], "TimeoutError")
            result = cast(JsonObject, await manager.call_tool("mcp__test__inspect", {}))
            self.assertIs(result["is_error"], True)
            self.assertEqual(result["data"], {"message": "expected failure"})
        finally:
            await manager.close()
