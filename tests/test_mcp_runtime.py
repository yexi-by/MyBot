"""通过本地 stdio 子进程验证 MCP 超时、恢复和错误结果。"""

import sys
import unittest
from typing import cast

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


class MCPRuntimeTest(unittest.IsolatedAsyncioTestCase):
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
