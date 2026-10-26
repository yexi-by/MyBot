import { useEffect, useSyncExternalStore } from "react";
import { errorMessage, get } from "./api";
import type {
  Coverage,
  GroupDirectory,
  GroupInfo,
  RuntimeEnvelope,
  SandboxStatus,
  ToolAccess,
  ToolsEnvelope,
} from "./types";

export interface ResourceState<T> {
  data: T | null;
  error: string;
  loading: boolean;
  updatedAt: number;
}

/**
 * 多个组件共享的只读接口数据。按订阅者要求的最短间隔轮询，
 * 标签页隐藏时暂停，重新可见时立即刷新。
 */
export class Resource<T> {
  state: ResourceState<T> = {
    data: null,
    error: "",
    loading: false,
    updatedAt: 0,
  };
  private listeners = new Set<() => void>();
  private intervals = new Map<symbol, number>();
  private timer: ReturnType<typeof setInterval> | undefined;
  private inflight: Promise<void> | null = null;

  constructor(private load: () => Promise<T>) {
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && this.intervals.size) void this.refresh();
      this.reschedule();
    });
  }
  private emit(next: Partial<ResourceState<T>>) {
    this.state = { ...this.state, ...next };
    this.listeners.forEach((listener) => listener());
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  snapshot = () => this.state;
  refresh = (): Promise<void> => {
    if (this.inflight) return this.inflight;
    this.emit({ loading: true });
    this.inflight = this.load()
      .then(
        (data) =>
          this.emit({ data, error: "", loading: false, updatedAt: Date.now() }),
        (error: unknown) =>
          this.emit({ error: errorMessage(error), loading: false }),
      )
      .finally(() => {
        this.inflight = null;
      });
    return this.inflight;
  };
  set(data: T) {
    this.emit({ data, error: "", updatedAt: Date.now() });
  }
  watch(key: symbol, interval: number) {
    const stale =
      !this.state.updatedAt ||
      (interval > 0 && Date.now() - this.state.updatedAt > interval);
    this.intervals.set(key, interval);
    this.reschedule();
    if (stale) void this.refresh();
  }
  unwatch(key: symbol) {
    this.intervals.delete(key);
    this.reschedule();
  }
  private reschedule() {
    clearInterval(this.timer);
    const active = [...this.intervals.values()].filter((value) => value > 0);
    if (!active.length || document.hidden) return;
    this.timer = setInterval(() => void this.refresh(), Math.min(...active));
  }
}

/** interval 为 0 表示只在首次使用时读取，之后靠手动刷新。 */
export function useResource<T>(
  resource: Resource<T>,
  interval = 0,
): ResourceState<T> {
  const state = useSyncExternalStore(resource.subscribe, resource.snapshot);
  useEffect(() => {
    const key = Symbol();
    resource.watch(key, interval);
    return () => resource.unwatch(key);
  }, [resource, interval]);
  return state;
}

export const runtimeResource = new Resource(() =>
  get<RuntimeEnvelope>("/api/runtime"),
);
export const sandboxResource = new Resource(() =>
  get<SandboxStatus>("/api/sandbox/status"),
);
export const toolsResource = new Resource(() =>
  get<ToolsEnvelope>("/api/tools"),
);
export const accessResource = new Resource(() =>
  get<ToolAccess>("/api/tools/access"),
);
export const coverageResource = new Resource(() =>
  get<Coverage>("/api/napcat/coverage"),
);
export const filesResource = new Resource(() =>
  get<{ files: string[] }>("/api/files/list").then((data) => data.files),
);

let forceGroups = false;
export const groupsResource = new Resource(() => {
  const url = "/api/napcat/groups" + (forceGroups ? "?refresh=true" : "");
  forceGroups = false;
  return get<GroupDirectory>(url);
});
export function reloadGroups() {
  forceGroups = true;
  return groupsResource.refresh();
}

/** 群号到 NapCat 返回的群资料；机器人离线时为空。 */
export function useGroupNames(): Map<string, GroupInfo> {
  const state = useResource(groupsResource, 60_000);
  const groups = state.data?.groups ?? [];
  return groupMap(groups);
}
let lastGroups: GroupInfo[] = [];
let lastMap = new Map<string, GroupInfo>();
function groupMap(groups: GroupInfo[]) {
  if (groups !== lastGroups) {
    lastGroups = groups;
    lastMap = new Map(groups.map((group) => [group.group_id, group]));
  }
  return lastMap;
}
