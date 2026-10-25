/** 侧栏导航与页面元信息。 */

import {
  Bell,
  Cloud,
  FileText,
  Image,
  MessageCircle,
  Plug,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Undo2,
  type LucideIcon,
} from "lucide-react";

import type { PageKey } from "@/schema";

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
    items: [
      { key: "plugin:ai_group_chat", label: "AI 群聊", group: "插件", table: "plugins.ai_group_chat", icon: MessageCircle, plugin: "ai_group_chat" },
      { key: "plugin:group_notice", label: "群通知", group: "插件", table: "plugins.group_notice", icon: Bell, plugin: "group_notice" },
      { key: "plugin:auto_unban", label: "自动解禁", group: "插件", table: "plugins.auto_unban", icon: ShieldCheck, plugin: "auto_unban" },
      { key: "plugin:image_generate", label: "生图", group: "插件", table: "plugins.image_generate", icon: Image, plugin: "image_generate" },
      { key: "plugin:neavo_image_generate", label: "Neavo 生图", group: "插件", table: "plugins.neavo_image_generate", icon: Sparkles, plugin: "neavo_image_generate" },
      { key: "plugin:recall_bot_image", label: "撤回图片", group: "插件", table: "plugins.recall_bot_image", icon: Undo2, plugin: "recall_bot_image" },
    ],
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
