/** 侧栏导航与页面元信息。 */

import {
  Bell,
  Cloud,
  FileText,
  MessageCircle,
  Plug,
  ShieldCheck,
  SlidersHorizontal,
  Undo2,
  type LucideIcon,
} from "lucide-react";

import type { PageKey } from "@/schema";
import { PLUGINS } from "@/schema/plugins";

import type { PluginId } from "./types";

export interface NavItem {
  key: PageKey;
  label: string;
  group: string;
  /** 顶栏面包屑和命令面板里显示的配置位置。 */
  table: string;
  icon: LucideIcon;
  plugin?: PluginId;
}

const PLUGIN_ICONS: Record<PluginId, LucideIcon> = {
  ai_group_chat: MessageCircle,
  group_notice: Bell,
  auto_unban: ShieldCheck,
  recall_bot_image: Undo2,
};

export const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "系统",
    items: [
      { key: "system", label: "系统设置", group: "系统", table: "app · server · napcat · database …", icon: SlidersHorizontal },
      { key: "providers", label: "模型服务", group: "系统", table: "llm.providers", icon: Cloud },
      { key: "mcp", label: "MCP 服务", group: "系统", table: "mcp", icon: Plug },
    ],
  },
  {
    title: "插件",
    items: PLUGINS.map(({ id, name }) => ({
      key: `plugin:${id}`,
      label: name,
      group: "插件",
      table: `plugins.${id}`,
      icon: PLUGIN_ICONS[id],
      plugin: id,
    })),
  },
  {
    title: "文件",
    items: [{ key: "files", label: "配置文本", group: "文件", table: "config/", icon: FileText }],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

export function navItem(key: PageKey): NavItem {
  return NAV_ITEMS.find((item) => item.key === key) ?? NAV_ITEMS[0]!;
}

export function cls(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}
