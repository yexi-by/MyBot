/** WebUI 后端 API 封装；409/422 以 ApiError 抛出。 */

import type {
  ConfigGetResponse,
  ConfigIssuePayload,
  ConfigSaveResponse,
  ConfigValidateResponse,
  FileGetResponse,
  FileListResponse,
  FileSaveResponse,
  MyBotConfigData,
  PowerResponse,
  ProviderModelsResponse,
} from "./types";

export class ApiError extends Error {
  status: number;
  issues: ConfigIssuePayload[];

  constructor(status: number, message: string, issues: ConfigIssuePayload[] = []) {
    super(message);
    this.status = status;
    this.issues = issues;
  }
}

interface ErrorDetail {
  detail?: string | { issues?: ConfigIssuePayload[] };
}

const REQUEST_TIMEOUT_MS = 30_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      ...init,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError(0, "请求超时，请检查服务状态");
    }
    throw error;
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    let issues: ConfigIssuePayload[] = [];
    try {
      const body = (await response.json()) as ErrorDetail;
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (body.detail?.issues) {
        issues = body.detail.issues;
        message = issues.map((issue) => `${issue.location}: ${issue.message}`).join("；");
      }
    } catch {
      // 保留默认错误消息
    }
    throw new ApiError(response.status, message, issues);
  }
  return (await response.json()) as T;
}

function fileApiPath(path: string): string {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export function getConfig(): Promise<ConfigGetResponse> {
  return request<ConfigGetResponse>("/api/config");
}

export function validateConfig(
  config: MyBotConfigData,
): Promise<ConfigValidateResponse> {
  return request<ConfigValidateResponse>("/api/config/validate", {
    method: "POST",
    body: JSON.stringify({ config }),
  });
}

export function saveConfig(
  config: MyBotConfigData,
  baseSha256: string,
): Promise<ConfigSaveResponse> {
  return request<ConfigSaveResponse>("/api/config", {
    method: "PUT",
    body: JSON.stringify({ config, base_sha256: baseSha256 }),
  });
}

export function listFiles(): Promise<FileListResponse> {
  return request<FileListResponse>("/api/files");
}

export function readFile(path: string): Promise<FileGetResponse> {
  return request<FileGetResponse>(`/api/files/${fileApiPath(path)}`);
}

export function saveFile(
  path: string,
  content: string,
  baseSha256: string | null,
): Promise<FileSaveResponse> {
  return request<FileSaveResponse>(`/api/files/${fileApiPath(path)}`, {
    method: "PUT",
    body: JSON.stringify({ content, base_sha256: baseSha256 }),
  });
}

export function restartSystem(): Promise<PowerResponse> {
  return request<PowerResponse>("/api/system/restart", { method: "POST" });
}

export function shutdownSystem(): Promise<PowerResponse> {
  return request<PowerResponse>("/api/system/shutdown", { method: "POST" });
}

export function getProviderModels(
  providerId: string,
): Promise<ProviderModelsResponse> {
  return request<ProviderModelsResponse>(
    `/api/llm/providers/${encodeURIComponent(providerId)}/models`,
  );
}
