/** Ctrl K 字段索引：静态字段 + 当前草稿里的 provider、MCP 服务和群。 */

import { getIn, isRecord, tomlPath } from "@/lib/path";
import { PLUGIN_IDS, type JsonObject, type PathSeg } from "@/lib/types";

import { groupName, type PageKey } from "./index";
import { AI_GROUPS_BASE, AI_SECTIONS, executionSection, GROUP_FIELDS, pluginMeta, PLUGIN_ROWS } from "./plugins";
import { MCP_SECTION, mcpServerFields, PROVIDER_FIELDS, type McpTransport } from "./services";
import { SYSTEM_SECTIONS } from "./system";
import { fieldPath, type FieldDef, type SectionDef } from "./types";

export interface FieldEntry {
  path: PathSeg[];
  label: string;
  /** 所在分节或条目，例如「图片处理」「Provider deepseek」。 */
  scope: string;
  field: FieldDef;
  page: PageKey;
  /** 完整 TOML 键，用于预览与搜索。 */
  toml: string;
  haystack: string;
}

function entry(base: readonly PathSeg[], field: FieldDef, scope: string, page: PageKey): FieldEntry {
  const path = fieldPath(base, field);
  const toml = tomlPath(path) || field.key;
  return {
    path,
    label: field.label,
    scope,
    field,
    page,
    toml,
    haystack: `${field.label} ${scope} ${toml} ${field.desc ?? ""}`.toLowerCase(),
  };
}

function fromSection(section: SectionDef, page: PageKey, config: JsonObject, scope = section.title): FieldEntry[] {
  if (section.optional && getIn(config, section.base) == null) return [];
  return section.fields.map((field) => entry(section.base, field, scope, page));
}

export function buildFieldIndex(config: JsonObject): FieldEntry[] {
  const out: FieldEntry[] = [];
  for (const section of SYSTEM_SECTIONS) out.push(...fromSection(section, "system", config));

  const providers = getIn(config, ["llm", "providers"]);
  if (isRecord(providers)) {
    for (const id of Object.keys(providers)) {
      for (const field of PROVIDER_FIELDS) out.push(entry(["llm", "providers", id], field, `Provider ${id}`, "providers"));
    }
  }

  out.push(...fromSection(MCP_SECTION, "mcp", config));
  const servers = getIn(config, ["mcp", "servers"]);
  if (isRecord(servers)) {
    for (const [name, server] of Object.entries(servers)) {
      const transport: McpTransport = isRecord(server) && server.transport === "streamable_http" ? "streamable_http" : "stdio";
      for (const field of mcpServerFields(transport)) out.push(entry(["mcp", "servers", name], field, `MCP ${name}`, "mcp"));
    }
  }

  for (const id of PLUGIN_IDS) {
    const page: PageKey = `plugin:${id}`;
    const name = pluginMeta(id).name;
    if (getIn(config, ["plugins", id]) != null) {
      const sections = id === "ai_group_chat" ? AI_SECTIONS : PLUGIN_ROWS[id].flat();
      for (const section of sections) {
        out.push(...fromSection(section, page, config, id === "ai_group_chat" ? section.title : `${name} · ${section.title}`));
      }
      if (id === "ai_group_chat") {
        const groups = getIn(config, AI_GROUPS_BASE);
        if (Array.isArray(groups)) {
          groups.forEach((_, index) => {
            for (const field of GROUP_FIELDS) {
              out.push(entry([...AI_GROUPS_BASE, index], field, groupName(config, index), page));
            }
          });
        }
      }
    }
    const execution = executionSection(id);
    for (const field of execution.fields) out.push(entry(execution.base, field, `${name} · 执行参数`, page));
  }
  return out;
}
