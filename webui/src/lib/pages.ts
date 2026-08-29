/** 页面导航元信息：图标、dirty 前缀映射、校验问题定位。 */

import { Cable, FileText, Plug, Puzzle, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { PLUGIN_METAS } from "./configMeta";
import type { PluginId } from "./types";

export type PageKey = "system" | "providers" | "mcp" | "files" | `plugin:${PluginId}`;

export interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    title: "系统",
    items: [
      { key: "system", label: "系统设置", icon: Settings },
      { key: "providers", label: "模型 Providers", icon: Cable },
      { key: "mcp", label: "MCP 服务", icon: Plug },
    ],
  },
  {
    title: "插件",
    items: PLUGIN_METAS.map((meta) => ({
      key: `plugin:${meta.id}` as PageKey,
      label: meta.name,
      icon: Puzzle,
    })),
  },
  {
    title: "配置文本",
    items: [{ key: "files", label: "配置文本文件", icon: FileText }],
  },
];

const PAGE_KEYS = new Set<string>(
  NAV_GROUPS.flatMap((group) => group.items.map((item) => item.key)),
);

export function pageFromLocation(): PageKey {
  const requested = new URLSearchParams(window.location.search).get("page") ?? "";
  return PAGE_KEYS.has(requested) ? (requested as PageKey) : "system";
}

/** 各页面包含的配置字段前缀，用于 dirty 计数与导航标记。 */
export function dirtyPrefixesForPage(key: PageKey): string[] {
  if (key === "system") {
    return [
      "app",
      "server",
      "napcat",
      "database",
      "network",
      "storage",
      "logging",
      "plugin_execution",
    ];
  }
  if (key === "providers") return ["llm"];
  if (key === "mcp") return ["mcp"];
  if (key.startsWith("plugin:")) return [`plugins.${key.slice(7)}`];
  return [];
}

/** 校验问题路径 → 所属页面。 */
export function pageForIssuePath(path: (string | number)[]): PageKey {
  const head = path[0];
  if (head === "llm") return "providers";
  if (head === "mcp") return "mcp";
  if (
    head === "plugins" &&
    typeof path[1] === "string" &&
    PLUGIN_METAS.some((meta) => meta.id === path[1])
  ) {
    return `plugin:${path[1]}` as PageKey;
  }
  return "system";
}

/** 系统页配置节 → SectionCard 标题，与 SystemPage 中的卡片标题保持一致。 */
const SECTION_CARD_TITLES: Record<string, string> = {
  app: "应用",
  server: "服务监听",
  napcat: "NapCat 连接",
  database: "数据库",
  network: "网络",
  storage: "图片存储",
  plugin_execution: "插件执行",
  logging: "日志",
};

/** 校验问题路径 → 滚动定位用的卡片标题（data-section）；无法确定时返回 null。 */
export function sectionTitleForIssue(
  path: (string | number)[],
): string | null {
  if (path[0] === "llm" && path[1] === "providers" && typeof path[2] === "string") {
    return path[2];
  }
  if (path[0] === "mcp" && path[1] === "servers" && typeof path[2] === "string") {
    return path[2];
  }
  if (typeof path[0] === "string") {
    return SECTION_CARD_TITLES[path[0]] ?? null;
  }
  return null;
}
