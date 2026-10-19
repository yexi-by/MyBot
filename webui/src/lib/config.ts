/**
 * mybot.toml 的编辑状态与自动保存：草稿、基线、乐观锁、写回回执和热生效跟踪都在这里。
 * 组件通过 useConfig 选择器订阅，字段只在自己的值或校验结果变化时重渲染。
 */

import {
  describePath,
  formatValue,
  humanizeIssue,
  lookupField,
  pageForPath,
  validateClient,
  type IssueMap,
  type PageKey,
} from "@/schema";

import { api, ApiError, errorMessage } from "./api";
import { deepEqual, diffLeaves, getIn, isRecord, pathKey, setIn, startsWith, type Leaf } from "./path";
import { Store, useStore } from "./store";
import { toast } from "./toast";
import type { ConfigGetResponse, ConfigIssue, ConfigMeta, JsonObject, Path, PathSeg } from "./types";

export const CONFIG_AUTOSAVE_MS = 800;
const APPLY_POLL_MS = 1000;
const APPLY_POLL_TIMES = 10;

export type SyncStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "saving" }
  | { kind: "saved"; at: string }
  | { kind: "watching" }
  | { kind: "applied"; revision: number }
  | { kind: "invalid" }
  | { kind: "conflict" }
  | { kind: "hold" }
  | { kind: "error"; message: string };

export interface Change {
  key: string;
  path: PathSeg[];
  label: string;
  from: string;
  to: string;
  restart: boolean;
  page: PageKey;
}

export interface Receipt {
  id: number;
  time: string;
  changes: Change[];
  state: "written" | "watching" | "applied" | "restart";
  revision?: number;
}

export interface ConfigState {
  phase: "loading" | "ready" | "failed" | "broken";
  loadError: string;
  draft: JsonObject;
  saved: JsonObject;
  sha256: string;
  meta: ConfigMeta | null;
  serverIssues: ConfigIssue[];
  clientIssues: IssueMap;
  status: SyncStatus;
  restartSections: string[];
  receipts: Receipt[];
  /** 非空表示 409 冲突对话框打开，内容是尚未写入的修改。 */
  conflict: Change[] | null;
  /** 选择「继续编辑」后：外部修改未处理，下次保存会覆盖。 */
  hold: boolean;
  /** 电源操作进行中，暂停自动保存。 */
  powerBusy: boolean;
}

const EMPTY: JsonObject = {};

export function clock(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

/* ---------- 派生数据（按引用缓存） ---------- */

const leavesCache = new WeakMap<object, { saved: object; leaves: Leaf[] }>();

/** 草稿相对基线的全部改动单元。 */
export function dirtyLeaves(state: Pick<ConfigState, "draft" | "saved">): Leaf[] {
  const cached = leavesCache.get(state.draft);
  if (cached && cached.saved === state.saved) return cached.leaves;
  const leaves = diffLeaves(state.saved, state.draft);
  leavesCache.set(state.draft, { saved: state.saved, leaves });
  return leaves;
}

const serverIndexCache = new WeakMap<ConfigIssue[], Map<string, string>>();

function serverIndex(issues: ConfigIssue[]): Map<string, string> {
  const cached = serverIndexCache.get(issues);
  if (cached) return cached;
  const index = new Map<string, string>();
  for (const issue of issues) {
    if (issue.path.length === 0) continue;
    const key = pathKey(issue.path);
    if (!index.has(key)) index.set(key, humanizeIssue(issue.error_type, issue.message));
  }
  serverIndexCache.set(issues, index);
  return index;
}

/** 字段的当前错误：本地预检优先，其次是后端最近一次校验结果。 */
export function issueAt(state: ConfigState, path: Path): string | null {
  const key = pathKey(path);
  return state.clientIssues.get(key) ?? serverIndex(state.serverIssues).get(key) ?? null;
}

export function issueCount(state: ConfigState): number {
  const keys = new Set(state.clientIssues.keys());
  for (const issue of state.serverIssues) keys.add(issue.path.length ? pathKey(issue.path) : `@${issue.location}`);
  return keys.size;
}

const TOGGLE_ROOTS = new Set([
  pathKey(["plugins", "ai_group_chat", "vision"]),
  pathKey(["plugins", "ai_group_chat", "recent_chat"]),
]);

function describeLeaf(leaf: Leaf, config: JsonObject, restartOnly: readonly string[]): Change {
  const field = lookupField(leaf.path, config)?.field ?? null;
  const isToggle =
    TOGGLE_ROOTS.has(pathKey(leaf.path)) || (leaf.path[0] === "plugins" && leaf.path.length === 2);
  const word = (value: unknown, side: "from" | "to") => {
    if (field === null && isRecord(value)) {
      if (isToggle) return "开启";
      return side === "to" ? "新增" : "已配置";
    }
    if (field === null && (value === undefined || value === null)) {
      if (isToggle) return "关闭";
      return side === "to" ? "删除" : "—";
    }
    return formatValue(value, field);
  };
  return {
    key: pathKey(leaf.path),
    path: leaf.path,
    label: describePath(leaf.path, config),
    from: word(leaf.before, "from"),
    to: word(leaf.after, "to"),
    restart: restartOnly.includes(String(leaf.path[0])),
    page: pageForPath(leaf.path),
  };
}

export function describeChanges(before: JsonObject, after: JsonObject, meta: ConfigMeta | null): Change[] {
  const restartOnly = meta?.restart_only_sections ?? [];
  return diffLeaves(before, after).map((leaf) => describeLeaf(leaf, after, restartOnly));
}

/* ---------- 控制器 ---------- */

class ConfigController {
  readonly store = new Store<ConfigState>({
    phase: "loading",
    loadError: "",
    draft: EMPTY,
    saved: EMPTY,
    sha256: "",
    meta: null,
    serverIssues: [],
    clientIssues: new Map(),
    status: { kind: "idle" },
    restartSections: [],
    receipts: [],
    conflict: null,
    hold: false,
    powerBusy: false,
  });

  private editVersion = 0;
  private blockedVersion: number | null = null;
  private timer = 0;
  private inflight: Promise<void> | null = null;
  private queued = false;
  private pollGeneration = 0;
  private receiptSeq = 1;

  get state(): ConfigState {
    return this.store.get();
  }

  isDirty(): boolean {
    return dirtyLeaves(this.state).length > 0;
  }

  isBusy(): boolean {
    return this.inflight !== null || this.timer !== 0;
  }

  async load(): Promise<void> {
    try {
      this.applySnapshot(await api.getConfig());
    } catch (error) {
      this.store.set({ phase: "failed", loadError: errorMessage(error, "加载配置失败") });
    }
  }

  /** 丢弃草稿，载入磁盘上的最新内容。 */
  async reload(): Promise<void> {
    this.applySnapshot(await api.getConfig());
  }

  private applySnapshot(fresh: ConfigGetResponse): void {
    window.clearTimeout(this.timer);
    this.timer = 0;
    this.pollGeneration += 1;
    this.editVersion += 1;
    this.blockedVersion = null;
    const decodeIssue = fresh.issues.find((issue) => issue.error_type === "toml_decode");
    this.store.set({
      phase: decodeIssue ? "broken" : "ready",
      loadError: decodeIssue ? decodeIssue.message : "",
      draft: fresh.config,
      saved: fresh.config,
      sha256: fresh.sha256,
      meta: fresh.meta,
      serverIssues: decodeIssue ? [] : fresh.issues,
      clientIssues: decodeIssue ? new Map() : validateClient(fresh.config),
      status: fresh.valid || decodeIssue ? { kind: "idle" } : { kind: "invalid" },
      restartSections: fresh.meta.restart_required_sections,
      conflict: null,
      hold: false,
    });
  }

  /** 外部（例如在配置文本里直接改 mybot.toml）写入后：没有本地修改时静默刷新。 */
  async refreshIfClean(): Promise<void> {
    if (this.isDirty() || this.isBusy()) return;
    try {
      this.applySnapshot(await api.getConfig());
    } catch {
      // 下一次保存遇到 409 时仍会走冲突流程。
    }
  }

  /* ----- 编辑 ----- */

  set(path: Path, value: unknown): void {
    this.update((draft) => setIn(draft, path, value), path);
  }

  update(fn: (draft: JsonObject) => JsonObject, touched?: Path): void {
    const state = this.state;
    if (state.phase !== "ready") return;
    const draft = fn(state.draft);
    if (draft === state.draft) return;
    this.editVersion += 1;
    const serverIssues = touched
      ? state.serverIssues.filter((issue) => !(issue.path.length > 0 && startsWith(issue.path, touched)))
      : state.serverIssues;
    this.store.set({
      draft,
      clientIssues: validateClient(draft),
      serverIssues: serverIssues.length === state.serverIssues.length ? state.serverIssues : serverIssues,
    });
    this.schedule();
  }

  private idleStatus(): SyncStatus {
    const state = this.state;
    if (state.hold) return { kind: "hold" };
    if (state.clientIssues.size > 0 || state.serverIssues.length > 0) return { kind: "invalid" };
    return state.status.kind === "saved" || state.status.kind === "applied" || state.status.kind === "watching"
      ? state.status
      : { kind: "idle" };
  }

  private schedule(): void {
    window.clearTimeout(this.timer);
    this.timer = 0;
    const state = this.state;
    if (state.phase !== "ready" || state.conflict) return;
    if (!this.isDirty()) {
      if (!this.inflight) this.store.set({ status: this.idleStatus() });
      return;
    }
    if (state.clientIssues.size > 0) {
      if (!this.inflight) this.store.set({ status: { kind: "invalid" } });
      return;
    }
    if (this.blockedVersion === this.editVersion || state.powerBusy) return;
    if (!this.inflight) this.store.set({ status: state.hold ? { kind: "hold" } : { kind: "pending" } });
    this.timer = window.setTimeout(() => {
      this.timer = 0;
      void this.save();
    }, CONFIG_AUTOSAVE_MS);
  }

  /* ----- 保存 ----- */

  save(): Promise<void> {
    window.clearTimeout(this.timer);
    this.timer = 0;
    if (this.inflight) {
      this.queued = true;
      return this.inflight;
    }
    const state = this.state;
    if (state.phase !== "ready" || state.conflict || !this.isDirty()) return Promise.resolve();
    if (state.clientIssues.size > 0) {
      this.store.set({ status: { kind: "invalid" } });
      return Promise.resolve();
    }
    this.inflight = this.runSave().finally(() => {
      this.inflight = null;
      if (this.queued) {
        this.queued = false;
        this.schedule();
      }
    });
    return this.inflight;
  }

  private async runSave(): Promise<void> {
    const state = this.state;
    const payload = state.draft;
    const baseline = state.saved;
    const saveVersion = this.editVersion;
    const previousRevision = state.meta?.plugin_revision ?? 0;
    const watcher = Boolean(state.meta?.watcher_active);
    this.store.set({ status: { kind: "saving" } });
    try {
      const result = await api.saveConfig(payload, state.sha256);
      const unchanged = this.editVersion === saveVersion;
      const changes = describeChanges(baseline, result.config, this.state.meta);
      const pluginsChanged = !deepEqual(baseline.plugins, result.config.plugins);
      const watching = watcher && pluginsChanged;
      const restartOnly = changes.length > 0 && changes.every((change) => change.restart);
      const receipt: Receipt = {
        id: this.receiptSeq++,
        time: clock(),
        changes,
        state: watching ? "watching" : restartOnly ? "restart" : "written",
      };
      this.blockedVersion = null;
      const now = this.state;
      this.store.set({
        saved: result.config,
        draft: unchanged ? result.config : now.draft,
        sha256: result.sha256,
        serverIssues: [],
        clientIssues: unchanged ? validateClient(result.config) : now.clientIssues,
        restartSections: result.restart_required_sections,
        receipts: changes.length > 0 ? [receipt, ...now.receipts].slice(0, 40) : now.receipts,
        hold: false,
        status: !unchanged ? { kind: "pending" } : watching ? { kind: "watching" } : { kind: "saved", at: receipt.time },
      });
      if (!unchanged) {
        this.queued = true;
        return;
      }
      if (watching) void this.pollApply(previousRevision, receipt.id);
    } catch (error) {
      this.blockedVersion = saveVersion;
      if (error instanceof ApiError && error.status === 409) {
        await this.openConflict();
      } else if (error instanceof ApiError && error.issues.length > 0) {
        this.store.set({ serverIssues: error.issues, status: { kind: "invalid" } });
        toast.error(`未写入：${error.issues.length} 处配置需要修正`);
      } else {
        const message = errorMessage(error, "保存失败");
        this.store.set({ status: { kind: "error", message } });
        toast.error(`未写入：${message}`);
      }
    }
  }

  /** 保存后轮询插件配置版本，确认热生效。 */
  private async pollApply(previousRevision: number, receiptId: number): Promise<void> {
    const generation = ++this.pollGeneration;
    const markReceipt = (patch: Partial<Receipt>) =>
      this.store.set((state) => ({
        receipts: state.receipts.map((item) => (item.id === receiptId ? { ...item, ...patch } : item)),
      }));
    for (let attempt = 0; attempt < APPLY_POLL_TIMES; attempt += 1) {
      await sleep(APPLY_POLL_MS);
      if (generation !== this.pollGeneration) return;
      try {
        const fresh = await api.getConfig();
        if (generation !== this.pollGeneration) return;
        this.store.set({ meta: fresh.meta, restartSections: fresh.meta.restart_required_sections });
        if (fresh.meta.plugin_revision > previousRevision) {
          markReceipt({ state: "applied", revision: fresh.meta.plugin_revision });
          if (this.state.status.kind === "watching") {
            this.store.set({ status: { kind: "applied", revision: fresh.meta.plugin_revision } });
          }
          return;
        }
      } catch {
        // 单次轮询失败不影响后续尝试。
      }
    }
    if (generation !== this.pollGeneration) return;
    markReceipt({ state: "written" });
    if (this.state.status.kind === "watching") this.store.set({ status: { kind: "saved", at: clock() } });
    toast.info("已写入，但 10 秒内没有检测到插件热载；可以在日志里确认原因");
  }

  /* ----- 冲突 ----- */

  private async openConflict(): Promise<void> {
    this.store.set({ status: { kind: "conflict" } });
    try {
      // 先拿最新 sha（不动表单值），三个出口都基于它走通。
      const fresh = await api.getConfig();
      this.store.set({ sha256: fresh.sha256, meta: fresh.meta, restartSections: fresh.meta.restart_required_sections });
    } catch {
      // 刷新失败时保留旧 sha；覆盖会再次 409 并重新弹窗。
    }
    const state = this.state;
    this.store.set({ conflict: describeChanges(state.saved, state.draft, state.meta) });
  }

  async resolveConflict(choice: "overwrite" | "discard" | "keep"): Promise<void> {
    if (choice === "overwrite") {
      this.store.set({ conflict: null, hold: false });
      this.blockedVersion = null;
      await this.save();
      return;
    }
    if (choice === "discard") {
      try {
        await this.reload();
        toast.success("已丢弃本地修改，载入外部修改后的 mybot.toml");
      } catch (error) {
        toast.error(`刷新失败：${errorMessage(error, "无法读取配置")}`);
      }
      return;
    }
    this.store.set({ conflict: null, hold: true, status: { kind: "hold" } });
  }

  /* ----- 快捷操作 ----- */

  /** Ctrl S：跳过防抖立即写入。 */
  async saveNow(): Promise<void> {
    const state = this.state;
    if (state.phase !== "ready") return;
    if (state.conflict) return;
    if (state.clientIssues.size > 0) {
      this.store.set({ status: { kind: "invalid" } });
      toast.error(`还有 ${state.clientIssues.size} 处需要修正，修正后会自动写入`);
      return;
    }
    if (!this.isDirty()) {
      toast.info("没有待写入的修改");
      return;
    }
    this.blockedVersion = null;
    await this.save();
  }

  /** 电源操作前：把待写入的修改立刻写完，返回是否已全部落盘。 */
  async flush(): Promise<boolean> {
    if (this.state.phase !== "ready") return true;
    if (this.timer !== 0 || this.inflight) {
      this.blockedVersion = null;
      await this.save();
      if (this.inflight) await this.inflight;
    }
    return !this.isDirty();
  }

  setPowerBusy(busy: boolean): void {
    this.store.set({ powerBusy: busy });
    if (!busy) this.schedule();
  }

  async validateNow(): Promise<void> {
    const state = this.state;
    if (state.phase !== "ready") return;
    try {
      const result = await api.validateConfig(state.draft);
      this.store.set({
        serverIssues: result.issues,
        status: result.valid && state.clientIssues.size === 0 ? this.idleAfterValidate() : { kind: "invalid" },
      });
      if (result.valid) toast.success("校验通过：当前配置可以写入");
      else toast.error(`发现 ${result.issues.length} 处配置问题`);
    } catch (error) {
      toast.error(`校验请求失败：${errorMessage(error, "无法连接")}`);
    }
  }

  private idleAfterValidate(): SyncStatus {
    if (this.isDirty()) return this.state.hold ? { kind: "hold" } : { kind: "pending" };
    return { kind: "idle" };
  }
}

export const config = new ConfigController();

/* ---------- Hooks ---------- */

export function useConfig<T>(selector: (state: ConfigState) => T, isEqual?: (a: T, b: T) => boolean): T {
  return useStore(config.store, selector, isEqual);
}

export function useValue(path: Path): unknown {
  return useConfig((state) => getIn(state.draft, path));
}

export function useFieldState(path: Path): { value: unknown; dirty: boolean; issue: string | null } {
  const value = useConfig((state) => getIn(state.draft, path));
  const dirty = useConfig((state) => !deepEqual(getIn(state.draft, path), getIn(state.saved, path)));
  const issue = useConfig((state) => issueAt(state, path));
  return { value, dirty, issue };
}

export function usePageDirtyCount(page: PageKey): number {
  return useConfig((state) => dirtyLeaves(state).filter((leaf) => pageForPath(leaf.path) === page).length);
}

export function useDirtyUnder(prefixes: readonly Path[]): boolean {
  return useConfig((state) =>
    dirtyLeaves(state).some((leaf) => prefixes.some((prefix) => startsWith(leaf.path, prefix))),
  );
}
