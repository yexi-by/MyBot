/** 路径路由：/system、/providers、/mcp、/plugins/<id>、/files?path=…；兼容旧版 ?page= 链接。 */

import type { PageKey } from "@/schema";

import { pathKey } from "./path";
import { Store, useStore } from "./store";
import { withTransition } from "./transition";
import { isPluginId, type Path } from "./types";

export interface Route {
  page: PageKey;
  /** 配置文本页要打开的文件。 */
  file?: string;
}

const DEFAULT_ROUTE: Route = { page: "system" };

function legacyPage(value: string): PageKey | null {
  if (value === "system" || value === "providers" || value === "mcp" || value === "files") return value;
  if (value.startsWith("plugin:") && isPluginId(value.slice(7))) return value as PageKey;
  return null;
}

export function parseLocation(location: Location = window.location): Route {
  const params = new URLSearchParams(location.search);
  const legacy = params.get("page");
  if (legacy) {
    const page = legacyPage(legacy);
    if (page) return { page };
  }
  const parts = location.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const [head, second] = parts;
  if (head === "system" || head === "providers" || head === "mcp") return { page: head };
  if (head === "files") return { page: "files", file: params.get("path") ?? undefined };
  if (head === "plugins" && second && isPluginId(second)) return { page: `plugin:${second}` };
  return DEFAULT_ROUTE;
}

export function hrefFor(route: Route): string {
  if (route.page === "files") {
    // 斜杠保留原样，地址栏里的文件路径更好读。
    return route.file ? `/files?path=${encodeURIComponent(route.file).replace(/%2F/g, "/")}` : "/files";
  }
  if (route.page.startsWith("plugin:")) return `/plugins/${route.page.slice(7)}`;
  return `/${route.page}`;
}

export const routerStore = new Store<{ route: Route }>({ route: parseLocation() });

// 首次加载时把旧链接和根路径规范成新地址，不新增历史记录。
{
  const canonical = hrefFor(routerStore.get().route);
  if (window.location.pathname + window.location.search !== canonical) {
    window.history.replaceState(null, "", canonical);
  }
}

const scrollMemory = new Map<string, number>();

function pageScroller(): HTMLElement | null {
  return document.getElementById("page");
}

function remember(route: Route): void {
  const scroller = pageScroller();
  if (scroller && route.page !== "files") scrollMemory.set(hrefFor(route), scroller.scrollTop);
}

function restore(route: Route, fromHistory: boolean): void {
  const scroller = pageScroller();
  if (!scroller || route.page === "files") return;
  scroller.scrollTop = fromHistory ? (scrollMemory.get(hrefFor(route)) ?? 0) : 0;
}

export function navigate(route: Route, options: { replace?: boolean; reveal?: Path } = {}): void {
  const current = routerStore.get().route;
  const same = current.page === route.page && (route.file === undefined || current.file === route.file);
  if (!same) {
    remember(current);
    const href = hrefFor(route);
    if (options.replace) window.history.replaceState(null, "", href);
    else window.history.pushState(null, "", href);
    withTransition(
      () => routerStore.set({ route }),
      () => restore(route, false),
    );
  }
  if (options.reveal) revealField(options.reveal);
}

/** 只同步地址栏，不做页面切换动画（例如在文件树里换文件）。 */
export function replaceRoute(route: Route): void {
  window.history.replaceState(null, "", hrefFor(route));
  routerStore.set({ route });
}

window.addEventListener("popstate", () => {
  const current = routerStore.get().route;
  remember(current);
  const route = parseLocation();
  withTransition(
    () => routerStore.set({ route }),
    () => restore(route, true),
  );
});

export function useRoute(): Route {
  return useStore(routerStore, (state) => state.route);
}

/** 滚动到字段并闪烁定位；目标可能要等页面渲染完才出现。 */
export function revealField(path: Path, attempts = 12): void {
  const selector = `[data-field][data-path="${CSS.escape(pathKey(path))}"]`;
  const element = document.querySelector<HTMLElement>(selector);
  if (!element) {
    if (attempts > 0) requestAnimationFrame(() => revealField(path, attempts - 1));
    else revealSection(path);
    return;
  }
  flash(element);
  const control = element.querySelector<HTMLElement>("input, textarea, select, button[role='switch'], button[role='radio']");
  control?.focus({ preventScroll: true });
}

/** 字段找不到时（例如分节级问题）退回到包含该路径的分节。 */
function revealSection(path: Path): void {
  for (let length = path.length; length > 0; length -= 1) {
    const key = pathKey(path.slice(0, length));
    const element = document.querySelector<HTMLElement>(`[data-section-path="${CSS.escape(key)}"]`);
    if (element) {
      flash(element);
      return;
    }
  }
}

function flash(element: HTMLElement): void {
  element.scrollIntoView({ block: "center", behavior: "smooth" });
  element.classList.remove("is-flash");
  void element.offsetWidth;
  element.classList.add("is-flash");
  window.setTimeout(() => element.classList.remove("is-flash"), 1700);
}

export function scrollToSection(id: string): void {
  const element = document.getElementById(`sec-${id}`);
  element?.scrollIntoView({ block: "start", behavior: "smooth" });
}
