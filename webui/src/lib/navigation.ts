import { useSyncExternalStore } from "react";
import type { MouseEvent } from "react";

const subscribers = new Set<() => void>();
let current = window.location.pathname + window.location.search;
let guard: (() => Promise<boolean>) | null = null;

/** 离开当前页面前的检查，例如文本编辑器还有未保存内容。 */
export function setNavigationGuard(next: (() => Promise<boolean>) | null) {
  guard = next;
}
function sync() {
  current = window.location.pathname + window.location.search;
  subscribers.forEach((callback) => callback());
}
window.addEventListener("popstate", () => {
  void (async () => {
    if (guard && !(await guard())) {
      history.pushState({}, "", current);
      return;
    }
    sync();
  })();
});

export async function navigate(
  path: string,
  options: { replace?: boolean; keepScroll?: boolean } = {},
): Promise<boolean> {
  if (path === current) return true;
  if (guard && !(await guard())) return false;
  const samePage = path.split("?")[0] === current.split("?")[0];
  if (options.replace) history.replaceState({}, "", path);
  else history.pushState({}, "", path);
  sync();
  if (!options.keepScroll && !samePage) window.scrollTo({ top: 0 });
  return true;
}

export function useLocation(): string {
  return useSyncExternalStore((callback) => {
    subscribers.add(callback);
    return () => subscribers.delete(callback);
  }, () => current);
}

export function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** 链接的点击处理：普通左键走前端路由，带修饰键时保留浏览器默认行为（新标签页等）。 */
export function linkHandler(path: string, after?: () => void) {
  return (event: MouseEvent) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    after?.();
    void navigate(path);
  };
}
