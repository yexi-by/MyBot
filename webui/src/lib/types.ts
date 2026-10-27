export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json };
export type ObjectValue = { [key: string]: Json };
export type Path = (string | number)[];

export interface Meta {
  boot_id: string;
  revision: number;
  config_file: string;
  restart_required: boolean;
  restart_sections: string[];
  sandbox_restart_required: boolean;
}
export interface ConfigEnvelope {
  config: ObjectValue;
  sha: string;
  meta: Meta;
}
export interface Issue {
  path: Path;
  message: string;
}
export interface SaveRecord {
  at: number;
  title: string;
  fields: string[];
}
export type ToolScope = "both" | "group" | "private" | "account";
export interface Tool {
  name: string;
  title: string;
  description: string;
  category: string;
  parameters: ObjectValue;
  effects: boolean;
  scope: ToolScope;
  requires?: string[];
}
export interface Category {
  id: string;
  label: string;
}
export interface ToolsEnvelope {
  tools: Tool[];
  categories: Category[];
}
export interface ToolAccess {
  scopes: Record<string, string[]>;
}
export interface FileEnvelope {
  path: string;
  content: string;
  sha: string;
}
export interface SandboxStatus {
  enabled: boolean;
  available: boolean;
  busy: boolean;
  quota_bytes: number;
  used_bytes: number | null;
  reason: string;
  restart_required: boolean;
  configuration_notice?: string;
  execution_id?: string | null;
}
export interface SandboxFile {
  path: string;
  name: string;
  size: number;
  is_dir: boolean;
}
export interface TurnContext {
  input_tokens?: number;
  max_tokens?: number;
  trigger_tokens?: number;
  compactions?: number;
  request_count?: number;
  usage_source?: string;
}
export interface Turn {
  scope_key: string;
  session_id?: string;
  turn_id?: string;
  state: string;
  started_at: number;
  rounds?: number;
  context?: TurnContext;
  /** 闲时压缩：会话闲置后在后台压缩上下文，期间到达的唤醒排队等待。 */
  idle?: boolean;
}
export type SessionState = "idle" | "queued" | "running" | "compacting";
export interface RetainedSession {
  scope_key: string;
  session_id: string;
  /** 当前上下文已接入的轮次；重置后为 0。 */
  turns: number;
  updated_at: number;
  state: SessionState;
  /** 排队等待下一轮的唤醒消息数。 */
  pending: number;
  last_outcome: string;
  context: TurnContext;
  retained_bytes: number;
  /** 上下文已在控制台重置，等待下次唤醒重新建立。 */
  cleared: boolean;
  model: string;
}
export interface SessionCapacity {
  sessions: number;
  max_sessions: number;
  bytes: number;
  max_bytes: number;
}
export interface QQMessage {
  message_id: string;
  sender_id: string;
  sender_name: string;
  time: number;
  segments: ObjectValue[];
}
export interface ToolOutcome {
  status: string;
  message?: string;
  data?: Json;
  error_type?: string | null;
  truncated?: boolean;
  preview?: string;
}
interface EntryBase {
  turn: number | null;
  at: number | null;
}
/** 会话上下文中的一条可见内容；推理与媒体原始数据不在其中。 */
export type SessionEntry = EntryBase &
  (
    | { kind: "system" | "transfer"; text: string }
    | { kind: "summary"; text: string; origin?: "turn" | "idle" | ""; model?: string }
    | { kind: "knowledge"; files: { path: string; text: string }[] }
    | { kind: "history"; message: QQMessage }
    | { kind: "wake"; messages: QQMessage[]; notice: string }
    | { kind: "media"; items: { type: string; mime: string; bytes: number }[] }
    | { kind: "notice"; messages: string[] }
    | { kind: "assistant"; text: string }
    | {
        kind: "call";
        id: string;
        name: string;
        arguments: Json;
        result: ToolOutcome | null;
        result_at: number | null;
      }
    | { kind: "result"; call_id: string; result: ToolOutcome }
    | {
        kind: "boundary";
        outcome: string;
        sent_receipts?: ObjectValue[];
        send_attempts?: number;
        unknown_operations?: ObjectValue[];
      }
    | { kind: "other"; label: string; text: string }
  );
export interface UnknownOperation {
  tool: string;
  arguments: Json;
  target: { bot_id: string; kind: string; peer_id: string };
}
export interface SessionDetail extends RetainedSession {
  version: number;
  pending_messages: QQMessage[];
  loaded_tools: string[];
  unknown_operations: UnknownOperation[];
  /** 与上次读取的会话和版本相同时为 null，沿用已有条目。 */
  entries: SessionEntry[] | null;
}
export interface Activity {
  id: string;
  time: number;
  scope_key: string;
  kind: string;
  message: string;
  status?: string;
  tool?: string;
}
export interface McpStatus {
  state: string;
  message?: string;
  error_type?: string;
  tools?: number;
}
export interface RuntimeEnvelope {
  started_at: number;
  turns: Turn[];
  sessions: RetainedSession[];
  capacity: SessionCapacity;
  activity: Activity[];
  napcat: {
    connected: string[];
    expected_bot_id: string;
    baseline_version: string;
  };
  mcp: Record<string, McpStatus>;
}
export interface GroupInfo {
  group_id: string;
  group_name: string;
  member_count: number | null;
  max_member_count: number | null;
}
export interface GroupDirectory {
  available: boolean;
  bot_id: string;
  reason: string;
  groups: GroupInfo[];
}
export interface CoverageAction {
  action: string;
  status: string;
  tool: string | null;
  reason: string;
  source?: string;
}
export interface Coverage {
  version: string;
  commit: string;
  source_url: string;
  aliases: string;
  actions: CoverageAction[];
}

export function object(value: Json | undefined): ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
}
export function list(value: Json | undefined): Json[] {
  return Array.isArray(value) ? value : [];
}
export function getAt(value: Json | undefined, path: Path): Json | undefined {
  let next = value;
  for (const key of path) {
    if (next === null || typeof next !== "object") return undefined;
    next = Array.isArray(next)
      ? next[Number(key)]
      : Object.hasOwn(next, String(key))
        ? next[String(key)]
        : undefined;
  }
  return next;
}
// 用 defineProperty 写入，避免群号等用户输入的键名触发原型链上的 setter。
function assign(target: ObjectValue, key: string, value: Json) {
  Object.defineProperty(target, key, {
    value,
    enumerable: true,
    writable: true,
    configurable: true,
  });
}
export function setAt(
  value: ObjectValue,
  path: Path,
  next: Json | undefined,
): ObjectValue {
  const copy = structuredClone(value);
  let node: ObjectValue | Json[] = copy;
  for (let i = 0; i < path.length - 1; i++) {
    const key = String(path[i]);
    if (Array.isArray(node)) {
      node = object(node[Number(key)]);
    } else {
      if (!Object.hasOwn(node, key) || node[key] === null) assign(node, key, {});
      node = node[key] as ObjectValue;
    }
  }
  const key = path.at(-1)!;
  if (Array.isArray(node)) {
    if (next === undefined) node.splice(Number(key), 1);
    else node[Number(key)] = next;
  } else if (next === undefined) delete node[String(key)];
  else assign(node, String(key), next);
  return copy;
}
export function equal(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
