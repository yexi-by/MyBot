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
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function arrayOf(value: unknown, check: (entry: unknown) => boolean): boolean {
  return Array.isArray(value) && value.every(check);
}
function validateEnvelope(url: string, value: unknown): void {
  const path = new URL(url, window.location.origin).pathname;
  let valid = record(value);
  if (valid && record(value)) {
    if (path === "/api/config")
      valid =
        record(value.config) &&
        typeof value.sha === "string" &&
        record(value.meta) &&
        typeof value.meta.boot_id === "string" &&
        typeof value.meta.revision === "number";
    else if (path === "/api/files/list")
      valid = arrayOf(value.files, (entry) => typeof entry === "string");
    else if (path === "/api/files")
      valid =
        typeof value.path === "string" &&
        typeof value.content === "string" &&
        typeof value.sha === "string";
    else if (path === "/api/tools")
      valid =
        arrayOf(value.categories, (entry) => typeof entry === "string") &&
        arrayOf(
          value.tools,
          (entry) =>
            record(entry) &&
            typeof entry.name === "string" &&
            typeof entry.description === "string" &&
            typeof entry.category === "string" &&
            record(entry.parameters) &&
            typeof entry.effects === "boolean" &&
            typeof entry.scope === "string",
        );
    else if (path === "/api/runtime")
      valid =
        record(value.napcat) &&
        arrayOf(value.turns, record) &&
        arrayOf(value.activity, record);
    else if (path === "/api/sandbox/status")
      valid =
        typeof value.available === "boolean" &&
        typeof value.busy === "boolean" &&
        typeof value.quota_bytes === "number" &&
        typeof value.used_bytes === "number";
    else if (path === "/api/sandbox/files")
      valid =
        typeof value.path === "string" &&
        arrayOf(
          value.files,
          (entry) =>
            record(entry) &&
            typeof entry.path === "string" &&
            typeof entry.name === "string" &&
            typeof entry.size === "number" &&
            typeof entry.is_dir === "boolean",
        );
    else if (path === "/api/napcat/coverage")
      valid =
        typeof value.version === "string" &&
        typeof value.commit === "string" &&
        typeof value.source_url === "string" &&
        arrayOf(
          value.actions,
          (entry) =>
            record(entry) &&
            typeof entry.action === "string" &&
            typeof entry.status === "string" &&
            typeof entry.reason === "string",
        );
    else if (/^\/api\/providers\/[^/]+\/models$/.test(path))
      valid = arrayOf(value.models, (entry) => typeof entry === "string");
  }
  if (!valid) throw new Error("服务响应格式不正确，请检查前后端版本是否一致");
}
export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const payload: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const data =
      payload && typeof payload === "object"
        ? (payload as Record<string, unknown>)
        : {};
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : typeof data.message === "string"
          ? data.message
          : "请求失败（" + response.status + "）";
    throw new ApiError(response.status, data, detail);
  }
  validateEnvelope(url, payload);
  return payload as T;
}
export const post = <T>(url: string, body: Json = {}) =>
  request<T>(url, { method: "POST", body: JSON.stringify(body) });
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
