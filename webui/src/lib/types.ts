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
}
export interface RetainedSession {
  scope_key: string;
  session_id: string;
  turns: number;
  updated_at: number;
  state: "running" | "idle";
  last_outcome: string;
  context: TurnContext;
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
