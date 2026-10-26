import type { Json } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public payload: Record<string, unknown>,
    message: string,
  ) {
    super(message);
  }
}

type Data = Record<string, unknown>;
const record = (value: unknown): value is Data =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown) => typeof value === "string";
const arrayOf = (value: unknown, check: (entry: unknown) => boolean) =>
  Array.isArray(value) && value.every(check);

// 只校验页面直接依赖的字段，用于尽早发现前后端版本不一致。
const shapes: [RegExp, (value: Data) => boolean][] = [
  [
    /^\/api\/config$/,
    (v) =>
      record(v.config) &&
      text(v.sha) &&
      record(v.meta) &&
      text(v.meta.boot_id) &&
      typeof v.meta.revision === "number" &&
      text(v.meta.config_file) &&
      Array.isArray(v.meta.restart_sections),
  ],
  [/^\/api\/files\/list$/, (v) => arrayOf(v.files, text)],
  [/^\/api\/files$/, (v) => text(v.path) && text(v.content) && text(v.sha)],
  [
    /^\/api\/tools$/,
    (v) =>
      arrayOf(v.categories, (c) => record(c) && text(c.id) && text(c.label)) &&
      arrayOf(
        v.tools,
        (t) =>
          record(t) &&
          text(t.name) &&
          text(t.title) &&
          text(t.description) &&
          text(t.category) &&
          record(t.parameters) &&
          typeof t.effects === "boolean" &&
          text(t.scope),
      ),
  ],
  [
    /^\/api\/tools\/access$/,
    (v) =>
      record(v.scopes) &&
      Object.values(v.scopes).every((names) => arrayOf(names, text)),
  ],
  [
    /^\/api\/runtime$/,
    (v) =>
      typeof v.started_at === "number" &&
      record(v.napcat) &&
      arrayOf(v.napcat.connected, text) &&
      arrayOf(v.turns, record) &&
      arrayOf(v.activity, record) &&
      record(v.mcp),
  ],
  [
    /^\/api\/napcat\/groups$/,
    (v) =>
      typeof v.available === "boolean" &&
      arrayOf(v.groups, (g) => record(g) && text(g.group_id)),
  ],
  [
    /^\/api\/sandbox\/status$/,
    (v) =>
      typeof v.available === "boolean" &&
      typeof v.busy === "boolean" &&
      typeof v.quota_bytes === "number" &&
      (v.used_bytes === null || typeof v.used_bytes === "number"),
  ],
  [
    /^\/api\/sandbox\/files$/,
    (v) =>
      text(v.path) &&
      arrayOf(
        v.files,
        (f) =>
          record(f) &&
          text(f.path) &&
          text(f.name) &&
          typeof f.size === "number" &&
          typeof f.is_dir === "boolean",
      ),
  ],
  [
    /^\/api\/napcat\/coverage$/,
    (v) =>
      text(v.version) &&
      text(v.source_url) &&
      arrayOf(
        v.actions,
        (a) => record(a) && text(a.action) && text(a.status) && text(a.reason),
      ),
  ],
  [/^\/api\/providers\/[^/]+\/models$/, (v) => arrayOf(v.models, text)],
];

function check(url: string, payload: unknown) {
  const path = new URL(url, window.location.origin).pathname;
  const shape = shapes.find(([pattern]) => pattern.test(path));
  if (shape && !(record(payload) && shape[1](payload)))
    throw new ApiError(
      -1,
      {},
      "服务返回的数据与当前页面不匹配。请刷新页面；仍出现时，请确认前后端是同一版本。",
    );
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, {}, "无法连接控制台服务，请确认服务正在运行");
  }
  const payload: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const data = record(payload) ? payload : {};
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : typeof data.message === "string"
          ? data.message
          : "请求失败（HTTP " + response.status + "）";
    throw new ApiError(response.status, data, detail);
  }
  check(url, payload);
  return payload as T;
}

export const get = <T>(url: string) => request<T>(url);
export const post = <T>(url: string, body: Json = {}) =>
  request<T>(url, { method: "POST", body: JSON.stringify(body) });
export const put = <T>(url: string, body: Json) =>
  request<T>(url, { method: "PUT", body: JSON.stringify(body) });

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
export function isOffline(error: unknown): boolean {
  return error instanceof ApiError && error.status === 0;
}
