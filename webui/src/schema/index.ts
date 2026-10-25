/** 字段表的查询：路径 → 字段定义 / 中文描述 / 所属页面，以及客户端预检。 */

import { deepEqual, getIn, isRecord, pathKey, startsWith } from "@/lib/path";
import { isPluginId, PLUGIN_IDS, type JsonObject, type Path, type PathSeg, type PluginId } from "@/lib/types";

import {
  AI_GROUPS_BASE,
  AI_SECTIONS,
  executionSection,
  GROUP_FIELDS,
  pluginMeta,
  PLUGIN_ROWS,
} from "./plugins";
import { MCP_SECTION, MCP_HTTP_FIELDS, MCP_STDIO_FIELDS, MCP_TRANSPORT, MCP_DISABLED, PROVIDER_FIELDS, mcpServerFields, type McpTransport } from "./services";
import { SYSTEM_SECTIONS } from "./system";
import { fieldPath, type FieldDef, type SectionDef } from "./types";

export type PageKey = "system" | "providers" | "mcp" | "files" | `plugin:${PluginId}`;

export const SECTION_LABELS: Record<string, string> = {
  app: "应用",
  server: "服务监听",
  napcat: "NapCat 连接",
  storage: "存储",
  network: "网络",
  logging: "日志",
  llm: "模型服务",
  mcp: "MCP 服务",
  database: "数据库",
  plugin_execution: "插件执行",
};

/** 某个配置路径归属的页面。 */
export function pageForPath(path: Path): PageKey {
  const [head, second, third] = path;
  if (head === "llm") return "providers";
  if (head === "mcp") return "mcp";
  if (head === "plugins" && typeof second === "string" && isPluginId(second)) return `plugin:${second}`;
  if (
    head === "plugin_execution" &&
    second === "plugins" &&
    typeof third === "string" &&
    isPluginId(third)
  ) {
    return `plugin:${third}`;
  }
  return "system";
}

export interface FieldHit {
  field: FieldDef;
  /** 字段所在分节或动态条目的名字，例如「图片处理」「Provider deepseek」。 */
  scope: string;
  section?: SectionDef;
}

const STATIC_SECTIONS: SectionDef[] = [
  ...SYSTEM_SECTIONS,
  MCP_SECTION,
  ...AI_SECTIONS,
  ...Object.values(PLUGIN_ROWS).flat(2),
];

const STATIC_INDEX = new Map<string, FieldHit>();
for (const section of STATIC_SECTIONS) {
  for (const field of section.fields) {
    STATIC_INDEX.set(pathKey(fieldPath(section.base, field)), { field, scope: section.title, section });
  }
}

function matchFields(rest: Path, fields: FieldDef[], scope: string): FieldHit | null {
  for (const field of fields) {
    const keyPath = field.key.split(".");
    if (startsWith(rest, keyPath)) return { field, scope };
  }
  return null;
}

/** 查找路径对应的字段；keyvalue / tags 的子路径归到所属字段。 */
export function lookupField(path: Path, config?: JsonObject): FieldHit | null {
  const exact = STATIC_INDEX.get(pathKey(path));
  if (exact) return exact;
  const [head, second, third] = path;
  if (head === "llm" && second === "providers" && typeof third === "string") {
    return matchFields(path.slice(3), PROVIDER_FIELDS, `Provider ${third}`);
  }
  if (head === "mcp" && second === "servers" && typeof third === "string") {
    return matchFields(
      path.slice(3),
      [MCP_TRANSPORT, MCP_DISABLED, ...MCP_STDIO_FIELDS, ...MCP_HTTP_FIELDS],
      `MCP ${third}`,
    );
  }
  if (startsWith(path, AI_GROUPS_BASE) && typeof path[AI_GROUPS_BASE.length] === "number") {
    const index = path[AI_GROUPS_BASE.length] as number;
    return matchFields(path.slice(AI_GROUPS_BASE.length + 1), GROUP_FIELDS, groupName(config, index));
  }
  if (head === "plugin_execution" && second === "plugins" && typeof third === "string" && isPluginId(third)) {
    const section = executionSection(third);
    const hit = matchFields(path.slice(3), section.fields, `${pluginMeta(third).name} · 执行参数`);
    return hit ? { ...hit, section } : null;
  }
  for (const section of STATIC_SECTIONS) {
    if (!startsWith(path, section.base)) continue;
    const hit = matchFields(path.slice(section.base.length), section.fields, section.title);
    if (hit) return { ...hit, section };
  }
  return null;
}

export function groupName(config: JsonObject | undefined, index: number): string {
  const id = config ? getIn(config, [...AI_GROUPS_BASE, index, "id"]) : undefined;
  return id !== undefined && id !== null && String(id).trim() !== "" ? `群 ${String(id)}` : `第 ${index + 1} 个群`;
}

/** 写回回执与问题列表里展示的中文路径名。 */
export function describePath(path: Path, config?: JsonObject): string {
  const [head, second, third] = path;
  if (head === "llm" && second === "providers" && typeof third === "string" && path.length === 3) {
    return `Provider ${third}`;
  }
  if (head === "mcp" && second === "servers" && typeof third === "string" && path.length === 3) {
    return `MCP 服务 ${third}`;
  }
  if (head === "plugins" && typeof second === "string" && isPluginId(second) && path.length === 2) {
    return `${pluginMeta(second).name}插件`;
  }
  if (startsWith(path, AI_GROUPS_BASE) && path.length === AI_GROUPS_BASE.length + 1) {
    return groupName(config, path[AI_GROUPS_BASE.length] as number);
  }
  if (head === "plugin_execution" && second === "plugins" && typeof third === "string" && path.length === 3) {
    return isPluginId(third) ? `${pluginMeta(third).name} · 执行参数` : `插件执行 · ${third}`;
  }
  const hit = lookupField(path, config);
  if (hit) {
    const extra = path.length > fieldDepth(hit, path) ? ` ${String(path[path.length - 1])}` : "";
    return `${hit.scope} · ${hit.field.label}${extra}`;
  }
  for (const section of STATIC_SECTIONS) {
    if (section.optional && deepEqual(section.base, path)) return section.title;
  }
  if (path.length === 1 && typeof head === "string" && SECTION_LABELS[head]) return SECTION_LABELS[head];
  return path.map(String).join(".");
}

function fieldDepth(hit: FieldHit, path: Path): number {
  // 子路径（例如 env 的某个变量）比字段本身更深时，在描述里补上键名。
  const keyLength = hit.field.key.split(".").length;
  if (hit.section) return hit.section.base.length + keyLength;
  const [head] = path;
  if (head === "llm" || head === "mcp" || head === "plugin_execution") return 3 + keyLength;
  return AI_GROUPS_BASE.length + 1 + keyLength;
}

/* ---------- 值的展示 ---------- */

export function humanBytes(value: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const text = Number.isInteger(size) ? String(size) : size.toFixed(size < 10 ? 2 : 1).replace(/\.?0+$/, "");
  return `${text} ${units[index]}`;
}

/** 数字字段旁的即时提示：0 / -1 的含义或字节换算。 */
export function numberHint(field: FieldDef, value: unknown): string {
  if (typeof value !== "number") return "";
  if (value === 0 && field.zero) return field.zero;
  if (value === -1 && field.neg) return field.neg;
  if (field.bytes && value >= 1024) return `≈ ${humanBytes(value)}`;
  return "";
}

export function formatValue(value: unknown, field?: FieldDef | null): string {
  if (value === undefined || value === null) return "（未设置）";
  if (field?.kind === "secret") return value === "" ? "（空）" : "••••••";
  if (field?.kind === "keyvalue" && isRecord(value)) {
    const keys = Object.keys(value);
    return keys.length ? keys.join(", ") : "（空）";
  }
  if (typeof value === "boolean") return value ? "开" : "关";
  if (typeof value === "number") {
    if (field) {
      if (value === 0 && field.zero) return `0（${field.zero}）`;
      if (value === -1 && field.neg) return `-1（${field.neg}）`;
      if (field.bytes && value >= 1024) return humanBytes(value);
      if (field.unit && field.unit !== "字节") return `${value} ${field.unit}`;
    }
    return String(value);
  }
  if (typeof value === "string") return value === "" ? "（空）" : value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "（空列表）";
    if (value.every((item) => typeof item !== "object")) return value.map(String).join(", ");
    return `${value.length} 项`;
  }
  return "{…}";
}

/* ---------- 客户端预检 ---------- */

export type IssueMap = Map<string, string>;

function checkNumber(field: FieldDef, value: unknown): string | null {
  if (value === undefined || value === null || value === "") return "必填";
  if (typeof value !== "number" || Number.isNaN(value)) return "需要是数字";
  if (!Number.isFinite(value)) return "需要是有限数字";
  if (field.integer && !Number.isInteger(value)) return "需要是整数";
  if (field.min !== undefined && value < field.min) return `不能小于 ${field.min}`;
  if (field.gt !== undefined && value <= field.gt) return `必须大于 ${field.gt}`;
  if (field.max !== undefined && value > field.max) return `不能大于 ${field.max}`;
  return null;
}

function checkList(field: FieldDef, value: unknown): string | null {
  if (value === undefined || value === null) return field.kind === "tags" && field.nullable ? null : "必填";
  if (!Array.isArray(value)) return "需要是列表";
  if (field.kind === "numtags") {
    for (const item of value) {
      if (typeof item !== "number" || !Number.isFinite(item)) return "每项都需要是数字";
      if (field.min !== undefined && item < field.min) return `每项都不能小于 ${field.min}`;
    }
    return null;
  }
  const texts = value.map((item) => String(item).trim());
  if (texts.some((item) => item === "")) return "不能包含空项";
  if (field.kind !== "ids" && !field.unique) return null;
  const normalized = field.kind === "ids" ? texts : texts.map((item) => item.toLowerCase());
  if (new Set(normalized).size !== normalized.length) return "不能包含重复项";
  return null;
}

export function checkField(field: FieldDef, value: unknown): string | null {
  switch (field.kind) {
    case "number":
      return checkNumber(field, value);
    case "tags":
    case "numtags":
    case "ids":
      return checkList(field, value);
    case "text":
    case "file":
    case "model":
    case "textarea":
      if (field.required && (typeof value !== "string" || value.trim() === "")) return "不能为空";
      return null;
    case "provider":
      if (typeof value !== "string" || value.trim() === "") return "请选择 Provider";
      return null;
    default:
      return null;
  }
}

function checkSection(issues: IssueMap, config: JsonObject, base: Path, fields: FieldDef[]) {
  for (const field of fields) {
    const path = [...base, ...field.key.split(".")];
    const message = checkField(field, getIn(config, path));
    if (message) issues.set(pathKey(path), message);
  }
}

function checkRetryPair(issues: IssueMap, config: JsonObject, base: Path, initial: string, max: string) {
  const first = getIn(config, [...base, initial]);
  const limit = getIn(config, [...base, max]);
  const key = pathKey([...base, max]);
  if (typeof first === "number" && typeof limit === "number" && limit > 0 && limit < first && !issues.has(key)) {
    issues.set(key, "不能小于初始重试间隔");
  }
}

/** 本地能判断的错误：数字范围、必填文本、列表重复、成对参数；其余交给后端 422。 */
export function validateClient(config: JsonObject): IssueMap {
  const issues: IssueMap = new Map();
  for (const section of SYSTEM_SECTIONS) checkSection(issues, config, section.base, section.fields);
  checkRetryPair(issues, config, ["napcat"], "send_retry_delay_seconds", "send_retry_max_delay_seconds");
  const password = getIn(config, ["database", "password"]);
  const passwordFile = getIn(config, ["database", "password_file"]);
  if (typeof password === "string" && password.trim() && typeof passwordFile === "string" && passwordFile.trim()) {
    issues.set(pathKey(["database", "password_file"]), "密码与密码文件只能填一个");
  }

  const providers = getIn(config, ["llm", "providers"]);
  if (isRecord(providers)) {
    for (const id of Object.keys(providers)) {
      const base = ["llm", "providers", id];
      checkSection(issues, config, base, PROVIDER_FIELDS);
      checkRetryPair(issues, config, base, "retry_delay_seconds", "retry_max_delay_seconds");
    }
  }

  checkSection(issues, config, MCP_SECTION.base, MCP_SECTION.fields);
  const servers = getIn(config, ["mcp", "servers"]);
  if (isRecord(servers)) {
    for (const [name, server] of Object.entries(servers)) {
      const transport: McpTransport = isRecord(server) && server.transport === "streamable_http" ? "streamable_http" : "stdio";
      checkSection(issues, config, ["mcp", "servers", name], mcpServerFields(transport));
    }
  }

  const plugins = getIn(config, ["plugins"]);
  for (const id of PLUGIN_IDS) {
    if (isRecord(plugins) && isRecord(plugins[id])) {
      const sections = id === "ai_group_chat" ? AI_SECTIONS : PLUGIN_ROWS[id].flat();
      for (const section of sections) {
        if (section.optional && getIn(config, section.base) == null) continue;
        checkSection(issues, config, section.base, section.fields);
      }
    }
    const execution = executionSection(id);
    if (getIn(config, execution.base) != null) checkSection(issues, config, execution.base, execution.fields);
  }
  if (isRecord(plugins) && isRecord(plugins.ai_group_chat)) {
    checkRetryPair(issues, config, ["plugins", "ai_group_chat", "vision"], "retry_delay_seconds", "retry_max_delay_seconds");
    const groups = getIn(config, AI_GROUPS_BASE);
    if (Array.isArray(groups)) {
      const seen = new Set<string>();
      groups.forEach((_, index) => {
        const base: PathSeg[] = [...AI_GROUPS_BASE, index];
        checkSection(issues, config, base, GROUP_FIELDS);
        const id = String(getIn(config, [...base, "id"]) ?? "").trim();
        const key = pathKey([...base, "id"]);
        if (id !== "" && seen.has(id) && !issues.has(key)) issues.set(key, "与前面的群号重复");
        seen.add(id);
      });
    }
  }
  return issues;
}

/* ---------- 后端问题的中文化 ---------- */

/** 把 Pydantic 英文报错收敛成短中文；后端已是中文的原样保留。 */
export function humanizeIssue(errorType: string, message: string): string {
  const stripped = message.replace(/^Value error, /, "").replace(/^Assertion failed, /, "");
  const number = /(-?\d+(?:\.\d+)?)/.exec(stripped)?.[1];
  switch (errorType) {
    case "missing":
      return "必填";
    case "extra_forbidden":
      return "不认识的字段，请在配置文本里删除";
    case "greater_than_equal":
      return number ? `不能小于 ${number}` : stripped;
    case "greater_than":
      return number ? `必须大于 ${number}` : stripped;
    case "less_than_equal":
      return number ? `不能大于 ${number}` : stripped;
    case "less_than":
      return number ? `必须小于 ${number}` : stripped;
    case "int_parsing":
    case "int_type":
    case "int_from_float":
      return "需要是整数";
    case "float_parsing":
    case "float_type":
    case "finite_number":
      return "需要是数字";
    case "bool_parsing":
    case "bool_type":
      return "需要是开关值";
    case "string_type":
      return "需要是文本";
    case "list_type":
    case "tuple_type":
      return "需要是列表";
    case "dict_type":
    case "model_type":
      return "需要是表";
    case "literal_error":
      return stripped.replace(/^Input should be /, "只能是 ").replace(/ or /g, " 或 ");
    default:
      return stripped;
  }
}

export const ALL_PLUGIN_IDS = PLUGIN_IDS;
