/** 与 app/webui/schemas.py 对应的 API 数据结构；配置本身按路径读写，不做逐字段类型声明。 */

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };

export type PathSeg = string | number;
export type Path = readonly PathSeg[];

export interface ConfigIssue {
  location: string;
  path: PathSeg[];
  error_type: string;
  message: string;
}

export interface ConfigMeta {
  plugin_revision: number;
  watcher_active: boolean;
  restart_only_sections: string[];
  restart_required_sections: string[];
  boot_id: string;
}

export interface ConfigGetResponse {
  config: JsonObject;
  sha256: string;
  valid: boolean;
  issues: ConfigIssue[];
  meta: ConfigMeta;
}

export interface ConfigValidateResponse {
  valid: boolean;
  issues: ConfigIssue[];
}

export interface ConfigSaveResponse {
  config: JsonObject;
  sha256: string;
  restart_required_sections: string[];
}

export interface FileListResponse {
  files: string[];
}

export interface FileGetResponse {
  path: string;
  content: string;
  sha256: string;
}

export interface FileSaveResponse {
  sha256: string;
}

export interface ProviderModelsResponse {
  provider: string;
  models: string[];
}

export interface PowerResponse {
  ok: boolean;
  action: "restart" | "shutdown";
  message: string;
}

export const PLUGIN_IDS = [
  "ai_group_chat",
  "group_notice",
  "auto_unban",
  "recall_bot_image",
] as const;

export type PluginId = (typeof PLUGIN_IDS)[number];

export function isPluginId(value: string): value is PluginId {
  return (PLUGIN_IDS as readonly string[]).includes(value);
}
