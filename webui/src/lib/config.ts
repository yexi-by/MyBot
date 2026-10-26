import { useSyncExternalStore } from "react";
import { ApiError, errorMessage, get, isOffline, put } from "./api";
import { equal, getAt, setAt } from "./types";
import type {
  ConfigEnvelope,
  Issue,
  Json,
  Meta,
  ObjectValue,
  Path,
  SaveRecord,
} from "./types";
import { fieldLabel, validateClient } from "../schema/fields";

/**
 * 配置草稿与自动保存。编辑停止约 0.9 秒后写回；外部修改时保留本地编辑，
 * 由用户在“覆盖 / 丢弃 / 继续编辑”中选择，继续编辑期间暂停自动保存。
 */
export interface ConfigState {
  loaded: boolean;
  loading: boolean;
  draft: ObjectValue;
  base: ObjectValue;
  sha: string;
  meta: Meta;
  saving: boolean;
  error: string;
  issues: Issue[];
  conflict: ConfigEnvelope | null;
  changed: Path[];
  history: SaveRecord[];
  repairPath: string;
  offline: boolean;
}
let state: ConfigState = {
  loaded: false,
  loading: false,
  draft: {},
  base: {},
  sha: "",
  meta: {
    boot_id: "",
    revision: 0,
    config_file: "",
    restart_required: false,
    restart_sections: [],
    sandbox_restart_required: false,
  },
  saving: false,
  error: "",
  issues: [],
  conflict: null,
  changed: [],
  history: [],
  repairPath: "",
  offline: false,
};
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | undefined;

function emit(update: Partial<ConfigState>) {
  state = { ...state, ...update };
  listeners.forEach((listener) => listener());
}
function diff(a: Json | undefined, b: Json | undefined, path: Path = []): Path[] {
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
  if (!state.conflict && state.changed.length && !state.issues.length && !state.offline)
    timer = setTimeout(() => void save(), 900);
}

export function useConfig(): ConfigState {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => state,
  );
}
export function getConfig(): ConfigState {
  return state;
}

export async function loadConfig() {
  emit({ loading: true });
  try {
    const data = await get<ConfigEnvelope>("/api/config");
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
      error: "",
      offline: false,
    });
  } catch (error) {
    const repair = error instanceof ApiError && typeof error.payload.repair_path === "string";
    emit({
      loading: false,
      error: repair ? invalidReason(error) : errorMessage(error),
      offline: isOffline(error),
      repairPath: repair ? String(error.payload.repair_path) : "",
    });
  }
}

export function change(path: Path, value: Json | undefined) {
  const draft = setAt(state.draft, path, value);
  emit({
    draft,
    changed: diff(state.base, draft),
    issues: validateClient(draft),
    error: state.conflict ? state.error : "",
  });
  schedule();
}

/** 配置无效（422）时的具体原因，最多列出两条。 */
function invalidReason(error: ApiError): string {
  const issues = serverIssues(error.payload);
  if (!issues.length) return error.message;
  return issues
    .slice(0, 2)
    .map((issue) => (issue.path.length ? issue.path.join(".") + "：" : "") + issue.message)
    .join("；");
}

function serverIssues(payload: Record<string, unknown>): Issue[] {
  const raw = payload.issues;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((entry) => ({
      path: Array.isArray(entry.path)
        ? (entry.path.filter(
            (part) => typeof part === "string" || typeof part === "number",
          ) as Path)
        : [],
      message: String(entry.message ?? "配置不符合要求"),
    }));
}

export async function save(): Promise<boolean> {
  clearTimeout(timer);
  if (!state.loaded || state.saving || state.conflict) return false;
  const issues = validateClient(state.draft);
  if (issues.length) {
    emit({ issues });
    return false;
  }
  if (!state.changed.length) return true;
  const submitted = structuredClone(state.draft);
  const fields = [...new Set(state.changed.map((path) => fieldLabel(submitted, path)))];
  emit({ saving: true, error: "", issues: [] });
  try {
    const data = await put<ConfigEnvelope>("/api/config", {
      config: submitted,
      sha: state.sha,
    });
    // 请求期间若又有新编辑，保留草稿并在下一次自动保存中提交。
    const draft = equal(submitted, state.draft) ? data.config : state.draft;
    emit({
      saving: false,
      offline: false,
      base: structuredClone(data.config),
      draft,
      sha: data.sha,
      meta: data.meta,
      changed: diff(data.config, draft),
      history: [{ at: Date.now(), title: "配置", fields }, ...state.history].slice(0, 20),
    });
    schedule();
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      try {
        const latest = await get<ConfigEnvelope>("/api/config");
        emit({ saving: false, conflict: latest, error: "配置文件已被外部修改，你的编辑仍保留" });
      } catch (refresh) {
        emit({ saving: false, error: "读取最新配置失败：" + errorMessage(refresh) });
      }
    } else {
      const issues = error instanceof ApiError ? serverIssues(error.payload) : [];
      emit({
        saving: false,
        offline: isOffline(error),
        error: isOffline(error)
          ? "无法连接控制台服务，恢复后会自动保存"
          : issues.length
            ? "服务端拒绝了这次保存，请修正标出的字段"
            : "保存失败：" + errorMessage(error),
        issues,
      });
    }
    return false;
  }
}

/**
 * 以最新文件为基础重新应用本地改动的字段后保存：外部修改的其他字段保留，
 * 同一字段以本地内容为准。再次遇到外部修改时仍会提示冲突。
 */
export async function overwriteConflict(): Promise<boolean> {
  if (!state.conflict) return false;
  const latest = await get<ConfigEnvelope>("/api/config");
  let draft = structuredClone(latest.config);
  for (const path of state.changed) draft = setAt(draft, path, getAt(state.draft, path));
  emit({
    sha: latest.sha,
    base: latest.config,
    meta: latest.meta,
    draft,
    changed: diff(latest.config, draft),
    issues: validateClient(draft),
    conflict: null,
    error: "",
  });
  return save();
}

/** 两份配置之间有差异的字段路径。 */
export function changedPaths(a: ObjectValue, b: ObjectValue): Path[] {
  return diff(a, b);
}

export function discardConflict() {
  const latest = state.conflict;
  if (!latest) return;
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
  if (state.saving || state.loading) return;
  if (!state.loaded) {
    await loadConfig();
    return;
  }
  try {
    const latest = await get<ConfigEnvelope>("/api/config");
    const recovered = state.offline || !!state.repairPath;
    if (recovered) emit({ offline: false, repairPath: "", error: state.conflict ? state.error : "" });
    if (state.conflict) {
      // 冲突期间只跟进最新版本，保证“覆盖”基于真正的当前文件。
      if (latest.sha !== state.conflict.sha) emit({ conflict: latest });
      return;
    }
    if (latest.sha !== state.sha) {
      if (state.changed.length) {
        clearTimeout(timer);
        emit({ conflict: latest, error: "配置文件已被外部修改，你的编辑仍保留" });
      } else
        emit({
          draft: latest.config,
          base: structuredClone(latest.config),
          sha: latest.sha,
          meta: latest.meta,
        });
    } else emit({ meta: latest.meta });
    if (recovered) schedule();
  } catch (error) {
    if (error instanceof ApiError && typeof error.payload.repair_path === "string")
      emit({ error: invalidReason(error), repairPath: error.payload.repair_path });
    else if (isOffline(error)) emit({ offline: true });
  }
}

export function recordSave(title: string, fields: string[]) {
  emit({ history: [{ at: Date.now(), title, fields }, ...state.history].slice(0, 20) });
}
