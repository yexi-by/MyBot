import type { Tone } from "./ui";
import type { ToolScope } from "./types";

export interface Label {
  text: string;
  tone: Tone;
}
function lookup(table: Record<string, Label>, key: string | undefined): Label {
  return (key && table[key]) || { text: key || "—", tone: "neutral" };
}

const activityKinds: Record<string, Label> = {
  turn: { text: "对话", tone: "neutral" },
  tool: { text: "工具调用", tone: "neutral" },
  compaction: { text: "上下文压缩", tone: "neutral" },
  session: { text: "会话回收", tone: "warning" },
  reset: { text: "会话重置", tone: "neutral" },
  resolution: { text: "动作核实", tone: "neutral" },
  config: { text: "配置", tone: "neutral" },
  error: { text: "错误", tone: "danger" },
  overload: { text: "排队已满", tone: "warning" },
  wake: { text: "唤醒", tone: "neutral" },
};
export const activityKind = (kind: string | undefined) =>
  lookup(activityKinds, kind);

const results: Record<string, Label> = {
  ok: { text: "成功", tone: "success" },
  completed: { text: "完成", tone: "success" },
  silent: { text: "未发送消息", tone: "neutral" },
  warning: { text: "提示", tone: "warning" },
  running: { text: "进行中", tone: "accent" },
  error: { text: "失败", tone: "danger" },
  unknown: { text: "结果未知", tone: "warning" },
  cancelled: { text: "已停止", tone: "neutral" },
  limit: { text: "达到上限", tone: "warning" },
  timeout: { text: "超时", tone: "warning" },
  reset: { text: "已重置", tone: "neutral" },
};
export const resultStatus = (status: string | undefined) =>
  lookup(results, status);

const turnStates: Record<string, Label> = {
  idle: { text: "等待唤醒", tone: "neutral" },
  queued: { text: "排队中", tone: "neutral" },
  running: { text: "运行中", tone: "accent" },
  compacting: { text: "压缩上下文", tone: "warning" },
};
export const turnState = (state: string | undefined) =>
  lookup(turnStates, state);

export const toolScopes: Record<ToolScope, string> = {
  both: "群聊和私聊",
  group: "仅群聊",
  private: "仅私聊",
  account: "机器人账号",
};

const mcpStates: Record<string, Label> = {
  connected: { text: "已连接", tone: "success" },
  connecting: { text: "连接中", tone: "accent" },
  reconnecting: { text: "重新连接中", tone: "warning" },
  stopped: { text: "已停止", tone: "neutral" },
  disabled: { text: "未启用", tone: "neutral" },
  pending_restart: { text: "重启后连接", tone: "warning" },
};
export const mcpState = (state: string | undefined) => lookup(mcpStates, state);

const coverageStates: Record<string, Label> = {
  implemented: { text: "已实现", tone: "success" },
  adapted: { text: "统一适配", tone: "accent" },
  alias: { text: "归并别名", tone: "neutral" },
  internal: { text: "内部使用", tone: "neutral" },
  excluded: { text: "不提供", tone: "neutral" },
};
export const coverageState = (status: string | undefined) =>
  lookup(coverageStates, status);

export const usageSources: Record<string, string> = {
  api_calibrated: "按接口返回用量校准",
  estimate: "本地估算",
};

export const restartSections: Record<string, string> = {
  server: "控制台服务",
  napcat: "NapCat 接入",
  mcp: "MCP 服务",
  sandbox: "共享沙箱",
  "runtime.concurrent_conversations": "并发会话数",
  "runtime.activity_limit": "活动记录条数",
};
