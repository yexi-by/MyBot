"""MCP 工具加载与 LLM 工具暴露。"""

from contextlib import AsyncExitStack
from typing import cast, override

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import Field

from app.models import JsonObject, JsonValue, StrictModel, to_json_value

from .schemas import LLMToolDefinition, LLMToolExecutor


class MCPServerConfig(StrictModel):
    """单个 MCP stdio 服务配置。"""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    disabled: bool = False


class MCPConfig(StrictModel):
    """MCP 总配置，读取 mcpServers 结构。"""

    enabled: bool = False
    mcpServers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class MCPToolManager(LLMToolExecutor):
    """将 MCP server 工具暴露为 LLM 可调用工具。"""

    def __init__(self, config: MCPConfig) -> None:
        """保存 MCP 配置。"""
        self.config: MCPConfig = config
        self._exit_stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}
        self._tools: list[LLMToolDefinition] = []
        self._started: bool = False

    @property
    def enabled(self) -> bool:
        """返回 MCP 是否启用。"""
        return self.config.enabled

    async def start(self) -> None:
        """启动已配置的 MCP server 并加载工具清单。"""
        if self._started:
            return
        self._started = True
        if not self.config.enabled:
            return
        self._exit_stack = AsyncExitStack()
        _ = await self._exit_stack.__aenter__()
        for server_name, server_config in self.config.mcpServers.items():
            if server_config.disabled:
                continue
            await self._connect_server(
                server_name=server_name, server_config=server_config
            )

    async def close(self) -> None:
        """关闭所有 MCP 会话和子进程。"""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._sessions.clear()
        self._tool_map.clear()
        self._tools.clear()
        self._started = False

    @override
    def list_tools(self) -> list[LLMToolDefinition]:
        """返回 MCP 工具定义。"""
        return self._tools[:]

    @override
    async def call_tool(self, name: str, arguments: JsonObject) -> JsonValue:
        """调用 MCP 工具。"""
        if not self._started:
            await self.start()
        mapping = self._tool_map.get(name)
        if mapping is None:
            raise KeyError(f"未知 MCP 工具: {name}")
        server_name, raw_tool_name = mapping
        session = self._sessions[server_name]
        result = await session.call_tool(raw_tool_name, cast(dict[str, object], arguments))
        return self._serialize_tool_result(result)

    async def _connect_server(
        self, *, server_name: str, server_config: MCPServerConfig
    ) -> None:
        """连接单个 MCP stdio server 并注册其工具。"""
        if self._exit_stack is None:
            raise RuntimeError("MCP exit stack 尚未初始化")
        server_params = StdioServerParameters(
            command=server_config.command,
            args=server_config.args,
            env=server_config.env,
            cwd=server_config.cwd,
        )
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        _ = await session.initialize()
        self._sessions[server_name] = session
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            self._register_mcp_tool(server_name=server_name, tool=tool)

    def _register_mcp_tool(self, *, server_name: str, tool: Tool) -> None:
        """把 MCP 工具转换成内部 LLM 工具定义。"""
        exposed_name = f"mcp__{server_name}__{tool.name}"
        if exposed_name in self._tool_map:
            raise ValueError(f"MCP 工具名重复: {exposed_name}")
        description = tool.description or tool.title or f"MCP 工具 {tool.name}"
        parameters = cast(JsonObject, tool.inputSchema)
        self._tool_map[exposed_name] = (server_name, tool.name)
        self._tools.append(
            LLMToolDefinition(
                name=exposed_name,
                description=description,
                parameters=parameters,
                strict=False,
            )
        )

    def _serialize_tool_result(self, result: CallToolResult) -> JsonValue:
        """将 MCP 调用结果收窄成 JSON 可序列化结构。"""
        if result.structuredContent is not None:
            return to_json_value(result.structuredContent)
        content: list[JsonValue] = []
        for item in result.content:
            if isinstance(item, TextContent):
                content.append({"type": "text", "text": item.text})
                continue
            content.append(to_json_value(item.model_dump(mode="json", by_alias=True)))
        return {"is_error": result.isError, "content": content}
