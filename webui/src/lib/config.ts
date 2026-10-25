import { useSyncExternalStore } from "react";
import { request, ApiError, errorMessage } from "./api";
import { equal, setAt } from "./types";
import type {
  ConfigEnvelope,
  Issue,
  Json,
  ObjectValue,
  Path,
  Receipt,
} from "./types";
import { fieldInstances, validateClient } from "../schema/fields";

export interface ConfigState {
  loaded: boolean;
  loading: boolean;
  draft: ObjectValue;
  base: ObjectValue;
  sha: string;
  meta: ConfigEnvelope["meta"];
  saving: boolean;
  error: string;
  issues: Issue[];
  conflict: ConfigEnvelope | null;
  receipts: Receipt[];
  changed: Path[];
  repairPath: string;
}
let state: ConfigState = {
  loaded: false,
  loading: false,
  draft: {},
  base: {},
  sha: "",
  meta: { boot_id: "", revision: 0, restart_required: false },
  saving: false,
  error: "",
  issues: [],
  conflict: null,
  receipts: [],
  changed: [],
  repairPath: "",
};
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | undefined;
function emit(update: Partial<ConfigState>) {
  state = { ...state, ...update };
  listeners.forEach((listener) => listener());
}
function diff(
  a: Json | undefined,
  b: Json | undefined,
  path: Path = [],
): Path[] {
  if (equal(a, b)) return [];
  if (
    a &&
    b &&
    typeof a === "object" &&
    typeof b === "object" &&
    !Array.isArray(a) &&
    !Array.isArray(b)
  )
    return [...new Set([...Object.keys(a), ...Object.keys(b)])].flatMap((key) =>
      diff(a[key], b[key], [...path, key]),
    );
  return [path];
}
function schedule() {
  clearTimeout(timer);
  if (!state.conflict && state.changed.length && !state.issues.length)
    timer = setTimeout(() => void save(), 900);
}
export function useConfig() {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => state,
  );
}
export function getConfig() {
  return state;
}
export async function loadConfig() {
  emit({ loading: true, error: "" });
  try {
    const data = await request<ConfigEnvelope>("/api/config");
    emit({
      loaded: true,
      loading: false,
      draft: data.config,
      base: structuredClone(data.config),
      sha: data.sha,
      meta: data.meta,
      changed: [],
      issues: [],
      conflict: null,
      repairPath: "",
    });
  } catch (error) {
    emit({
      loading: false,
      error: errorMessage(error),
      repairPath:
        error instanceof ApiError &&
        typeof error.payload.repair_path === "string"
          ? error.payload.repair_path
          : "",
    });
  }
}
export function change(path: Path, value: Json | undefined) {
  const draft = setAt(state.draft, path, value);
  emit({
    draft,
    changed: diff(state.base, draft),
    issues: validateClient(draft),
    error: state.conflict ? "外部配置已变化；你的编辑仍保留" : "",
  });
  schedule();
}
function serverIssues(payload: Record<string, unknown>): Issue[] {
  const raw = payload.issues ?? payload.detail;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const entry = item as Record<string, unknown>;
      const location = entry.path ?? entry.loc;
      return {
        path: Array.isArray(location)
          ? (location.filter(
              (part) => typeof part === "string" || typeof part === "number",
            ) as Path)
          : [],
        message: String(entry.message ?? entry.msg ?? "配置不符合要求"),
      };
    });
}
export async function save(): Promise<boolean> {
  clearTimeout(timer);
  if (!state.loaded || state.saving || state.conflict) return false;
  const issues = validateClient(state.draft);
  if (issues.length) {
    emit({ issues, error: "请先修正标出的配置字段" });
    return false;
  }
  if (!state.changed.length) return true;
  const submitted = structuredClone(state.draft),
    fields = state.changed.map((path) => {
      const descriptor = fieldInstances(submitted).find((item) =>
        equal(item.path, path),
      );
      return descriptor?.field.label || path.join(".");
    });
  emit({ saving: true, error: "", issues: [] });
  try {
    const data = await request<ConfigEnvelope>("/api/config", {
      method: "PUT",
      body: JSON.stringify({ config: submitted, sha: state.sha }),
    });
    const draft = equal(submitted, state.draft) ? data.config : state.draft;
    emit({
      saving: false,
      base: structuredClone(data.config),
      draft,
      sha: data.sha,
      meta: data.meta,
      changed: diff(data.config, draft),
      receipts: [
        {
          at: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
          message: "配置已写入",
          fields: [...new Set(fields)],
        },
        ...state.receipts,
      ].slice(0, 30),
    });
    schedule();
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      try {
        const latest = await request<ConfigEnvelope>("/api/config");
        emit({
          saving: false,
          conflict: latest,
          sha: latest.sha,
          error: "外部配置已变化；你的编辑仍保留",
        });
      } catch (refreshError) {
        emit({
          saving: false,
          error: "读取冲突版本失败：" + errorMessage(refreshError),
        });
      }
    } else
      emit({
        saving: false,
        error: errorMessage(error),
        issues: error instanceof ApiError ? serverIssues(error.payload) : [],
      });
    return false;
  }
}
export async function overwriteConflict() {
  if (!state.conflict) return;
  // 使用最新 sha；始终保留本地表单内容。再次发生变化仍会返回 409。
  const latest = await request<ConfigEnvelope>("/api/config");
  emit({
    sha: latest.sha,
    base: latest.config,
    changed: diff(latest.config, state.draft),
    conflict: null,
    error: "",
  });
  return save();
}
export function discardConflict() {
  if (!state.conflict) return;
  const latest = state.conflict;
  emit({
    draft: latest.config,
    base: structuredClone(latest.config),
    sha: latest.sha,
    meta: latest.meta,
    changed: [],
    issues: [],
    conflict: null,
    error: "",
  });
}
export async function pollConfig() {
  if (state.saving || state.conflict || state.loading) return;
  if (!state.loaded) {
    if (state.repairPath) await loadConfig();
    return;
  }
  try {
    const latest = await request<ConfigEnvelope>("/api/config");
    if (state.repairPath) emit({ repairPath: "", error: "" });
    if (latest.sha !== state.sha) {
      if (state.changed.length) {
        clearTimeout(timer);
        emit({
          conflict: latest,
          sha: latest.sha,
          error: "检测到外部修改；本地编辑已保留",
        });
      } else
        emit({
          draft: latest.config,
          base: structuredClone(latest.config),
          sha: latest.sha,
          meta: latest.meta,
        });
    } else emit({ meta: latest.meta });
  } catch (error) {
    if (
      error instanceof ApiError &&
      typeof error.payload.repair_path === "string"
    )
      emit({ error: error.message, repairPath: error.payload.repair_path });
  }
}
export function recordReceipt(message: string, fields: string[]) {
  emit({
    receipts: [
      {
        at: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        message,
        fields,
      },
      ...state.receipts,
    ].slice(0, 30),
  });
}
