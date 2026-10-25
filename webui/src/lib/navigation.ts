import { useSyncExternalStore } from "react";
const subscribers = new Set<() => void>();
let currentLocation = window.location.pathname + window.location.search;
let navigationGuard: (() => Promise<boolean>) | null = null;
export function setNavigationGuard(guard: (() => Promise<boolean>) | null) {
  navigationGuard = guard;
}
function locationKey() {
  return currentLocation;
}
function notify() {
  currentLocation = window.location.pathname + window.location.search;
  subscribers.forEach((callback) => callback());
}
window.addEventListener("popstate", () => {
  void (async () => {
    if (navigationGuard && !(await navigationGuard())) {
      history.pushState({}, "", currentLocation);
      return;
    }
    notify();
  })();
});
export function transition(action: () => void) {
  if (
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
    document.startViewTransition
  ) {
    const change = document.startViewTransition(action);
    void change.ready.catch(() => {});
    void change.finished.catch(() => {});
  } else action();
}
export async function navigate(path: string) {
  if (path === locationKey()) return;
  if (navigationGuard && !(await navigationGuard())) return;
  transition(() => {
    history.pushState({}, "", path);
    notify();
    window.scrollTo({ top: 0 });
  });
}
export function useLocation() {
  return useSyncExternalStore((callback) => {
    subscribers.add(callback);
    return () => subscribers.delete(callback);
  }, locationKey);
}
export const pages = [
  {
    path: "/connections",
    name: "连接与模型",
    code: "01",
    description: "NapCat 与模型协议",
  },
  {
    path: "/groups",
    name: "群配置",
    code: "02",
    description: "按群授予 Agent 能力",
  },
  {
    path: "/private",
    name: "统一私聊",
    code: "03",
    description: "所有私聊使用同一策略",
  },
  {
    path: "/tools",
    name: "工具目录",
    code: "04",
    description: "渐进披露与参数边界",
  },
  { path: "/mcp", name: "MCP 服务", code: "05", description: "连接外部工具" },
  {
    path: "/persona",
    name: "人格与知识",
    code: "06",
    description: "默认耄耋与知识文件",
  },
  {
    path: "/sandbox",
    name: "共享沙箱",
    code: "07",
    description: "代码、依赖与产物",
  },
  {
    path: "/runtime",
    name: "当前运行",
    code: "08",
    description: "内存中的近期活动",
  },
];
export function pageForPath(path: string) {
  return pages.find((page) => path.startsWith(page.path)) || pages[0];
}
export function fieldRoute(path: (string | number)[]) {
  if (path[0] === "groups")
    return "/groups?id=" + encodeURIComponent(String(path[1]));
  if (path[0] === "providers")
    return "/connections?provider=" + encodeURIComponent(String(path[1]));
  if (path[0] === "mcp")
    return "/mcp?id=" + encodeURIComponent(String(path[1]));
  if (path[0] === "private") return "/private";
  if (path[0] === "sandbox") return "/sandbox";
  if (path[0] === "persona_file" || path[0] === "knowledge_files")
    return "/persona";
  if (path[0] === "runtime") return "/runtime";
  return "/connections";
}
export function fieldId(path: (string | number)[]) {
  return (
    "field-" + path.map((part) => encodeURIComponent(String(part))).join("--")
  );
}
