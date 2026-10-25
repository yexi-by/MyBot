/** 模型服务（llm.providers）与 MCP 服务（mcp）的字段表；键名是用户自定义的动态 ID。 */

import type { JsonObject } from "@/lib/types";

import { int, keyvalue, num, secret, seg, sw, tags, text } from "./helpers";
import type { FieldDef, SectionDef } from "./types";

export const PROVIDER_FIELDS: FieldDef[] = [
  secret("api_key", "API Key", { span: 2, desc: "留空表示上游服务不需要鉴权" }),
  text("base_url", "Base URL", { mono: true, nullable: true, placeholder: "https://api.example.com/v1" }),
  text("proxy", "Provider 代理", { mono: true, nullable: true, placeholder: "http://127.0.0.1:7890", desc: "留空时按「继承全局代理」决定" }),
  sw("inherit_network_proxy", "继承全局代理", { desc: "Provider 代理为空时使用「系统设置 · 网络」的代理" }),
  num("timeout_seconds", "请求超时", { unit: "秒", min: 0, zero: "使用全局网络超时" }),
  int("max_attempts", "最大尝试次数", { unit: "次", min: 1 }),
  num("retry_delay_seconds", "初始重试间隔", { unit: "秒", min: 0 }),
  num("retry_max_delay_seconds", "最大重试间隔", { unit: "秒", min: 0, zero: "不限制退避上限" }),
];

export function defaultProvider(): JsonObject {
  return {
    inherit_network_proxy: true,
    timeout_seconds: 0,
    max_attempts: 5,
    retry_delay_seconds: 0,
    retry_max_delay_seconds: 0,
  };
}

export const MCP_SECTION: SectionDef = {
  id: "mcp",
  title: "MCP 总开关",
  base: ["mcp"],
  restart: true,
  desc: "MCP 工具以 mcp__{server}__{tool} 的形式提供给 AI 调用。",
  fields: [
    sw("enabled", "启用 MCP", { desc: "关闭后不连接任何 MCP 服务" }),
    num("initialization_timeout_seconds", "初始化超时", { unit: "秒", gt: 0, desc: "每个服务完成握手并加载工具清单的总期限" }),
    num("call_timeout_seconds", "工具调用超时", { unit: "秒", gt: 0, desc: "超时会作为可恢复错误返回给 AI" }),
  ],
};

export type McpTransport = "stdio" | "streamable_http";

export const MCP_TRANSPORT = seg("transport", "连接方式", [
  ["stdio", "本地进程"],
  ["streamable_http", "HTTP 服务"],
], { span: 2 });

export const MCP_DISABLED = sw("disabled", "禁用此服务", { desc: "保留配置但不连接" });

export const MCP_STDIO_FIELDS: FieldDef[] = [
  text("command", "启动命令", { mono: true, placeholder: "npx", required: true }),
  text("cwd", "工作目录", { mono: true, nullable: true, span: 2, placeholder: "留空使用 MyBot 进程的当前目录" }),
  tags("args", "命令参数", { span: 3, mono: true, nullable: true, placeholder: "-y", desc: "按顺序传给启动命令；回车添加一项", addLabel: "添加参数" }),
  keyvalue("env", "环境变量", { addLabel: "添加环境变量" }),
];

export const MCP_HTTP_FIELDS: FieldDef[] = [
  text("url", "服务地址", { mono: true, span: 3, placeholder: "https://mcp.example.com/mcp", required: true, desc: "完整的 HTTP 或 HTTPS 地址；认证信息写在请求头里" }),
  keyvalue("headers", "请求头", { addLabel: "添加请求头", desc: "请求头的值按密钥处理，界面默认遮挡" }),
];

export function mcpServerFields(transport: McpTransport): FieldDef[] {
  return [MCP_TRANSPORT, MCP_DISABLED, ...(transport === "stdio" ? MCP_STDIO_FIELDS : MCP_HTTP_FIELDS)];
}

export function defaultMcpServer(transport: McpTransport, disabled = false): JsonObject {
  return transport === "stdio"
    ? { transport, command: "", args: [], disabled }
    : { transport, url: "", disabled };
}
