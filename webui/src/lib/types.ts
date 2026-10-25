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
  restart_required: boolean | string[];
  sandbox_restart_required?: boolean;
}
export interface ConfigEnvelope {
  config: ObjectValue;
  sha: string;
  meta: Meta;
  schema?: ObjectValue;
}
export interface Issue {
  path: Path;
  message: string;
}
export interface Receipt {
  at: string;
  message: string;
  fields: string[];
}
export interface Tool {
  name: string;
  description: string;
  category: string;
  parameters: ObjectValue;
  effects: boolean;
  scope: string;
  allowed?: boolean;
  reason?: string;
}
export interface ToolsEnvelope {
  tools: Tool[];
  categories: string[];
}
export interface FileEnvelope {
  path: string;
  content: string;
  sha: string;
}
export interface SandboxStatus {
  available: boolean;
  reason?: string;
  quota_bytes: number;
  used_bytes: number;
  busy: boolean;
  execution_id?: string | null;
  configuration_notice?: string;
  restart_required?: boolean;
}
export interface SandboxFile {
  path: string;
  name: string;
  size: number;
  is_dir: boolean;
}
export interface RuntimeEnvelope {
  napcat: Json;
  turns: ObjectValue[];
  activity: ObjectValue[];
}
export function object(value: Json | undefined): ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
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
      if (!Object.hasOwn(node, key) || node[key] === null) {
        Object.defineProperty(node, key, {
          value: {},
          enumerable: true,
          writable: true,
          configurable: true,
        });
      }
      node = node[key] as ObjectValue;
    }
  }
  const key = path.at(-1)!;
  if (Array.isArray(node)) {
    if (next === undefined) node.splice(Number(key), 1);
    else node[Number(key)] = next;
  } else if (next === undefined) delete node[String(key)];
  else
    Object.defineProperty(node, String(key), {
      value: next,
      enumerable: true,
      writable: true,
      configurable: true,
    });
  return copy;
}
export function equal(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
export function formatBytes(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n < 1000) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1000,
    i = 0;
  while (v >= 1000 && i < 3) {
    v /= 1000;
    i++;
  }
  return v.toFixed(v >= 100 ? 0 : 1) + " " + units[i];
}
