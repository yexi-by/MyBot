import {
  Box,
  FileText,
  LayoutDashboard,
  MessagesSquare,
  Settings,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { navigate, prefersReducedMotion } from "./lib/navigation";
import type { Path } from "./lib/types";
import { fieldId, routeForPath } from "./schema/fields";

export type SettingsSection =
  | "napcat"
  | "runtime"
  | "server"
  | "config-file"
  | "appearance"
  | "service";
export const settingsSections: { id: SettingsSection; label: string }[] = [
  { id: "napcat", label: "NapCat 接入" },
  { id: "runtime", label: "运行限制" },
  { id: "server", label: "控制台服务" },
  { id: "config-file", label: "配置文件" },
  { id: "appearance", label: "外观" },
  { id: "service", label: "服务控制" },
];

export type Route =
  | { page: "overview" }
  | {
      page: "conversations";
      kind: "group" | "private" | null;
      id: string;
      tab: "general" | "permissions";
    }
  | { page: "models"; id: string }
  | { page: "prompts"; path: string }
  | { page: "tools"; view: "catalog" | "mcp" | "napcat"; id: string; tool: string }
  | { page: "sandbox"; view: "overview" | "settings" }
  | { page: "settings"; section: SettingsSection }
  | { page: "missing" };

const decode = (value: string | undefined) => {
  try {
    return decodeURIComponent(value ?? "");
  } catch {
    return "";
  }
};

export function parseRoute(location: string): Route {
  const url = new URL(location, window.location.origin);
  const parts = url.pathname.split("/").filter(Boolean);
  const query = url.searchParams;
  const [head, second, third] = parts;
  if (!head) return { page: "overview" };
  if (head === "conversations") {
    const tab = query.get("tab") === "permissions" ? "permissions" : "general";
    if (second === "private" && parts.length === 2)
      return { page: "conversations", kind: "private", id: "", tab };
    if (second === "group" && third && parts.length === 3)
      return { page: "conversations", kind: "group", id: decode(third), tab };
    if (!second) return { page: "conversations", kind: null, id: "", tab };
  }
  if (head === "models" && parts.length <= 2) return { page: "models", id: decode(second) };
  if (head === "prompts" && parts.length === 1)
    return { page: "prompts", path: query.get("path") ?? "" };
  if (head === "tools") {
    const tool = query.get("tool") ?? "";
    if (!second) return { page: "tools", view: "catalog", id: "", tool };
    if (second === "mcp" && parts.length <= 3)
      return { page: "tools", view: "mcp", id: decode(third), tool };
    if (second === "napcat" && parts.length === 2)
      return { page: "tools", view: "napcat", id: "", tool };
  }
  if (head === "sandbox") {
    if (!second) return { page: "sandbox", view: "overview" };
    if (second === "settings" && parts.length === 2) return { page: "sandbox", view: "settings" };
  }
  if (head === "settings") {
    const section = settingsSections.find((item) => item.id === (second ?? "napcat"));
    if (section && parts.length <= 2) return { page: "settings", section: section.id };
  }
  return { page: "missing" };
}

/** 旧版控制台的地址，书签和外部链接仍可打开。 */
export function legacyRedirect(location: string): string | null {
  const url = new URL(location, window.location.origin);
  const query = url.searchParams;
  const enc = encodeURIComponent;
  switch (url.pathname) {
    case "/connections": {
      const provider = query.get("provider");
      return provider ? "/models/" + enc(provider) : "/models";
    }
    case "/groups": {
      const id = query.get("id");
      return id ? "/conversations/group/" + enc(id) : "/conversations";
    }
    case "/private":
      return "/conversations/private";
    case "/mcp": {
      const id = query.get("id");
      return id ? "/tools/mcp/" + enc(id) : "/tools/mcp";
    }
    case "/persona":
      return "/prompts";
    case "/files": {
      const path = query.get("path") ?? "";
      if (path.toLowerCase().endsWith(".toml")) return "/settings/config-file";
      return path ? "/prompts?path=" + enc(path) : "/prompts";
    }
    case "/runtime":
      return "/";
    default:
      return null;
  }
}

export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  page: Route["page"];
}
export const navItems: NavItem[] = [
  { path: "/", label: "概览", icon: LayoutDashboard, page: "overview" },
  { path: "/conversations", label: "会话", icon: MessagesSquare, page: "conversations" },
  { path: "/models", label: "模型", icon: Sparkles, page: "models" },
  { path: "/prompts", label: "提示词", icon: FileText, page: "prompts" },
  { path: "/tools", label: "工具", icon: Wrench, page: "tools" },
  { path: "/sandbox", label: "沙箱", icon: Box, page: "sandbox" },
  { path: "/settings", label: "设置", icon: Settings, page: "settings" },
];

/** 跳到字段所在页面并聚焦；用于错误列表、保存记录和命令面板。 */
export async function focusField(path: Path) {
  if (!(await navigate(routeForPath(path)))) return;
  const id = fieldId(path);
  // 页面切换后等字段渲染出来再定位，最多等待约 1 秒。
  for (let attempt = 0; attempt < 20; attempt++) {
    const element = document.getElementById(id);
    if (element) {
      element.scrollIntoView({
        behavior: prefersReducedMotion() ? "instant" : "smooth",
        block: "center",
      });
      element.focus({ preventScroll: true });
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}
