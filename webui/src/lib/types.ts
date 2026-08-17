/** 与 app/config/schemas.py 对应的配置类型，以及 WebUI API 载荷类型。 */

export type AppEnvironment = "development" | "staging" | "production" | "test";
export type UvicornLogLevel =
  | "critical"
  | "error"
  | "warning"
  | "info"
  | "debug"
  | "trace";
export type LogLevelName =
  | "TRACE"
  | "DEBUG"
  | "INFO"
  | "SUCCESS"
  | "WARNING"
  | "ERROR"
  | "CRITICAL";

export interface AppConfig {
  name?: string;
  environment?: AppEnvironment;
}

export interface ServerConfig {
  host?: string;
  port?: number;
  websocket_path_prefix?: string;
  access_log?: boolean;
  log_level?: UvicornLogLevel;
}

export interface NapCatConfig {
  websocket_token?: string | null;
  send_max_attempts?: number;
  send_retry_delay_seconds?: number;
}

export interface ImageStorageConfig {
  directory?: string;
  download_concurrency?: number;
  download_timeout_seconds?: number;
  max_bytes?: number;
  retry_delays_seconds?: number[];
  lease_seconds?: number;
}

export interface StorageConfig {
  images?: ImageStorageConfig;
}

export interface DatabaseConfig {
  host?: string;
  port?: number;
  name?: string;
  user?: string;
  password?: string | null;
  password_file?: string | null;
  pool_size?: number;
  max_overflow?: number;
  pool_timeout_seconds?: number;
  statement_timeout_seconds?: number;
}

export interface NetworkConfig {
  proxy?: string | null;
  timeout_seconds?: number;
}

export interface LoggingConfig {
  directory?: string;
  console_level?: LogLevelName;
  file_level?: LogLevelName;
  rotation?: string;
  retention?: string;
  compression?: string;
}

export interface LLMProviderConfig {
  api_key?: string | null;
  base_url?: string | null;
  max_attempts?: number;
  retry_delay_seconds?: number;
}

export interface LLMServiceConfig {
  providers?: Record<string, LLMProviderConfig>;
}

export interface MCPServerConfig {
  command: string;
  args?: string[];
  env?: Record<string, string> | null;
  cwd?: string | null;
  disabled?: boolean;
}

export interface MCPConfig {
  enabled?: boolean;
  servers?: Record<string, MCPServerConfig>;
}

export interface ModelRef {
  provider: string;
  name: string;
}

export interface ChatModelRef extends ModelRef {
  supports_images?: boolean;
}

export interface AIGroupConfig {
  id: string;
  system_prompt_file: string;
  knowledge_base_file?: string | null;
  max_context_tokens: number;
}

export interface AIVisionConfig {
  model: ModelRef;
  system_prompt_file: string;
  user_prompt_file: string;
  max_attempts?: number;
  retry_delay_seconds?: number;
  retain_descriptions?: boolean;
}

export interface AIImageConfig {
  max_per_turn?: number;
  fetch_concurrency?: number;
  download_timeout_seconds?: number;
  forward_tool_enabled?: boolean;
  forward_max_per_call?: number;
  forward_max_per_turn?: number;
}

export interface AIGroupChatConfig {
  model: ChatModelRef;
  vision?: AIVisionConfig | null;
  images?: AIImageConfig;
  max_tool_rounds?: number;
  token_safety_factor?: number;
  context_compression_notice?: string;
  max_reply_chars?: number;
  show_reasoning?: boolean;
  retain_reasoning?: boolean;
  debug_dump_messages?: boolean;
  extra_requirements_file?: string;
  allow_mention_all?: boolean;
  retain_tool_results?: boolean;
  groups?: AIGroupConfig[];
}

export interface GroupNoticeConfig {
  groups?: string[];
  send_avatar?: boolean;
}

export interface AutoUnbanConfig {
  protected_users?: string[];
}

export interface ImageGenerateConfig {
  groups?: string[];
  model: ModelRef;
  fetch_concurrency?: number;
  download_timeout_seconds?: number;
}

export interface NeavoImageGenerateConfig {
  groups?: string[];
  base_url: string;
  api_token?: string | null;
  poll_interval_seconds: number;
  generation_timeout_seconds: number;
  request_timeout_seconds: number;
  max_image_bytes: number;
}

export type EmptyPluginConfig = Record<string, never>;

export interface PluginsConfig {
  ai_group_chat?: AIGroupChatConfig | null;
  group_notice?: GroupNoticeConfig | null;
  auto_unban?: AutoUnbanConfig | null;
  image_generate?: ImageGenerateConfig | null;
  neavo_image_generate?: NeavoImageGenerateConfig | null;
  recall_bot_image?: EmptyPluginConfig | null;
}

export interface MyBotConfigData {
  app?: AppConfig;
  server?: ServerConfig;
  napcat: NapCatConfig;
  storage?: StorageConfig;
  network?: NetworkConfig;
  logging?: LoggingConfig;
  llm?: LLMServiceConfig;
  mcp?: MCPConfig;
  database: DatabaseConfig;
  plugins?: PluginsConfig;
}

export type PluginId = keyof PluginsConfig;

/* ---- API 载荷 ---- */

export interface ConfigIssuePayload {
  location: string;
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
  config: MyBotConfigData;
  sha256: string;
  valid: boolean;
  issues: ConfigIssuePayload[];
  meta: ConfigMeta;
}

export interface ConfigValidateResponse {
  valid: boolean;
  issues: ConfigIssuePayload[];
}

export interface ConfigSaveResponse {
  config: MyBotConfigData;
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

export interface PowerResponse {
  ok: boolean;
  action: "restart" | "shutdown";
  message: string;
}
