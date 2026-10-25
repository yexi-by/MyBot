/** WebUI 后端 API 调用；错误统一收敛为 ApiError（status 0 表示连不上服务）。 */

import type {
  ConfigGetResponse,
  ConfigIssue,
  ConfigSaveResponse,
  ConfigValidateResponse,
  FileGetResponse,
  FileListResponse,
  FileSaveResponse,
  JsonObject,
  PowerResponse,
  ProviderModelsResponse,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly issues: ConfigIssue[];

  constructor(status: number, message: string, issues: ConfigIssue[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.issues = issues;
  }
}

function isIssueList(value: unknown): value is ConfigIssue[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        "location" in item &&
        "error_type" in item &&
        "message" in item,
    )
  );
}

function errorFromDetail(status: number, detail: unknown): ApiError {
  if (typeof detail === "string") return new ApiError(status, detail);
  if (typeof detail === "object" && detail !== null && "issues" in detail) {
    const issues = (detail as { issues: unknown }).issues;
    if (isIssueList(issues)) {
      const normalized = issues.map((issue) => ({
        ...issue,
        path: Array.isArray(issue.path) ? issue.path : [],
      }));
      return new ApiError(status, `${normalized.length} 个配置问题`, normalized);
    }
  }
  if (Array.isArray(detail)) return new ApiError(status, "请求格式不被后端接受");
  return new ApiError(status, `请求失败（HTTP ${status}）`);
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      cache: "no-store",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "连不上 MyBot 服务");
  }
  if (response.ok) return (await response.json()) as T;
  let detail: unknown = null;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail ?? null;
  } catch {
    // 非 JSON 错误体（例如反向代理返回的 HTML）只保留状态码。
  }
  throw errorFromDetail(response.status, detail);
}

/** 文本文件路径逐段编码，保留目录分隔符。 */
function filePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export const api = {
  getConfig: () => request<ConfigGetResponse>("GET", "/api/config"),
  validateConfig: (config: JsonObject) =>
    request<ConfigValidateResponse>("POST", "/api/config/validate", { config }),
  saveConfig: (config: JsonObject, baseSha256: string) =>
    request<ConfigSaveResponse>("PUT", "/api/config", { config, base_sha256: baseSha256 }),
  listFiles: () => request<FileListResponse>("GET", "/api/files"),
  readFile: (path: string) => request<FileGetResponse>("GET", `/api/files/${filePath(path)}`),
  saveFile: (path: string, content: string, baseSha256: string | null) =>
    request<FileSaveResponse>("PUT", `/api/files/${filePath(path)}`, {
      content,
      base_sha256: baseSha256,
    }),
  listModels: (provider: string) =>
    request<ProviderModelsResponse>(
      "GET",
      `/api/llm/providers/${encodeURIComponent(provider)}/models`,
    ),
  restart: () => request<PowerResponse>("POST", "/api/system/restart"),
  shutdown: () => request<PowerResponse>("POST", "/api/system/shutdown"),
};

export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
