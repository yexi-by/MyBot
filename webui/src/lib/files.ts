/** config/ 文本文件（提示词、知识库、mybot.toml 原文）的打开、自动保存与冲突处理。 */

import { requiredPromptFiles } from "@/schema/plugins";

import { api, ApiError, errorMessage } from "./api";
import { clock, config } from "./config";
import { Store, useStore } from "./store";
import { toast } from "./toast";

export const TEXT_AUTOSAVE_MS = 1000;
export const CONFIG_FILE = "mybot.toml";

export type FileStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "saving" }
  | { kind: "saved"; at: string }
  | { kind: "invalid"; message: string }
  | { kind: "conflict" }
  | { kind: "hold" }
  | { kind: "error"; message: string };

export interface OpenFile {
  path: string;
  content: string;
  /** 最近一次读取或写入成功时的内容，用于判断是否有未保存修改。 */
  savedContent: string;
  sha256: string;
}

export type FileView = "edit" | "split" | "preview";

export interface FilesState {
  list: string[] | null;
  listError: string;
  open: OpenFile | null;
  loading: string | null;
  status: FileStatus;
  conflictOpen: boolean;
  view: FileView;
  /** 当前文件有未保存修改时，等待确认的切换目标。 */
  pendingSwitch: string | null;
  /** 打开后的文件版本号，编辑器据此判断是否需要整体替换内容。 */
  revision: number;
}

function initialView(): FileView {
  try {
    const stored = localStorage.getItem("mybot.files.view");
    if (stored === "edit" || stored === "split" || stored === "preview") return stored;
  } catch {
    // 无法访问 localStorage 时使用默认视图。
  }
  return "split";
}

class FilesController {
  readonly store = new Store<FilesState>({
    list: null,
    listError: "",
    open: null,
    loading: null,
    status: { kind: "idle" },
    conflictOpen: false,
    view: initialView(),
    pendingSwitch: null,
    revision: 0,
  });

  private editVersion = 0;
  private blockedVersion: number | null = null;
  private timer = 0;
  private inflight: Promise<void> | null = null;
  private loadGeneration = 0;

  get state(): FilesState {
    return this.store.get();
  }

  isDirty(): boolean {
    const open = this.state.open;
    return open !== null && open.content !== open.savedContent;
  }

  isBusy(): boolean {
    return this.inflight !== null;
  }

  async refreshList(): Promise<void> {
    try {
      const { files } = await api.listFiles();
      this.store.set({ list: files, listError: "" });
    } catch (error) {
      this.store.set({ listError: errorMessage(error, "读取文件列表失败") });
    }
  }

  setView(view: FileView): void {
    this.store.set({ view });
    try {
      localStorage.setItem("mybot.files.view", view);
    } catch {
      // 忽略隐私模式下的存储失败。
    }
  }

  /** 打开文件：当前文件有待保存的修改时先写完；写不进去（为空、冲突、失败）再请求确认。 */
  async requestOpen(path: string): Promise<void> {
    if (this.state.open?.path === path) return;
    if (this.isDirty() || this.inflight) {
      const clean = await this.flush();
      if (!clean) {
        this.store.set({ pendingSwitch: path });
        return;
      }
    }
    await this.load(path);
  }

  cancelSwitch(): void {
    this.store.set({ pendingSwitch: null });
  }

  confirmSwitch(): void {
    const target = this.state.pendingSwitch;
    this.store.set({ pendingSwitch: null });
    if (target) void this.load(target);
  }

  async load(path: string): Promise<void> {
    const generation = ++this.loadGeneration;
    this.store.set({ loading: path });
    try {
      const file = await api.readFile(path);
      if (generation !== this.loadGeneration) return;
      window.clearTimeout(this.timer);
      this.timer = 0;
      this.editVersion = 0;
      this.blockedVersion = null;
      this.store.set((state) => ({
        open: { path: file.path, content: file.content, savedContent: file.content, sha256: file.sha256 },
        loading: null,
        status: { kind: "idle" },
        conflictOpen: false,
        revision: state.revision + 1,
      }));
    } catch (error) {
      if (generation !== this.loadGeneration) return;
      this.store.set({ loading: null });
      toast.error(`打不开 ${path}：${errorMessage(error, "读取失败")}`);
    }
  }

  edit(content: string): void {
    const open = this.state.open;
    if (!open || open.content === content) return;
    this.editVersion += 1;
    this.store.set({ open: { ...open, content } });
    this.schedule();
  }

  private requiredEmptyMessage(open: OpenFile): string | null {
    if (open.content.trim() !== "") return null;
    return requiredPromptFiles(config.state.draft).has(open.path)
      ? "这是插件引用的提示词，不能保存为空"
      : null;
  }

  private schedule(): void {
    window.clearTimeout(this.timer);
    this.timer = 0;
    const state = this.state;
    const open = state.open;
    if (!open || state.conflictOpen) return;
    if (open.content === open.savedContent) {
      if (!this.inflight && state.status.kind !== "saved") this.store.set({ status: { kind: "idle" } });
      return;
    }
    const empty = this.requiredEmptyMessage(open);
    if (empty) {
      this.store.set({ status: { kind: "invalid", message: empty } });
      return;
    }
    if (this.blockedVersion === this.editVersion) return;
    if (!this.inflight) this.store.set({ status: state.status.kind === "hold" ? state.status : { kind: "pending" } });
    this.timer = window.setTimeout(() => {
      this.timer = 0;
      void this.save();
    }, TEXT_AUTOSAVE_MS);
  }

  save(overwrite = false): Promise<void> {
    window.clearTimeout(this.timer);
    this.timer = 0;
    if (this.inflight) return this.inflight;
    const open = this.state.open;
    if (!open || open.content === open.savedContent) return Promise.resolve();
    const empty = this.requiredEmptyMessage(open);
    if (empty) {
      this.store.set({ status: { kind: "invalid", message: empty } });
      toast.error(empty);
      return Promise.resolve();
    }
    this.inflight = this.runSave(open, overwrite).finally(() => {
      this.inflight = null;
    });
    return this.inflight;
  }

  private async runSave(request: OpenFile, overwrite: boolean): Promise<void> {
    const saveVersion = this.editVersion;
    this.store.set({ status: { kind: "saving" } });
    try {
      const base = overwrite ? (await api.readFile(request.path)).sha256 : request.sha256;
      const result = await api.saveFile(request.path, request.content, base);
      this.blockedVersion = null;
      const latest = this.state.open;
      if (latest?.path !== request.path) return;
      const unchanged = this.editVersion === saveVersion;
      this.store.set({
        open: { ...latest, sha256: result.sha256, savedContent: request.content },
        status: unchanged ? { kind: "saved", at: clock() } : { kind: "pending" },
        conflictOpen: false,
      });
      if (!unchanged) this.schedule();
      if (request.path === CONFIG_FILE) void this.afterConfigFileSaved();
    } catch (error) {
      this.blockedVersion = saveVersion;
      if (error instanceof ApiError && error.status === 409) {
        this.store.set({ status: { kind: "conflict" } });
        try {
          const fresh = await api.readFile(request.path);
          const latest = this.state.open;
          if (latest?.path === request.path) this.store.set({ open: { ...latest, sha256: fresh.sha256 } });
        } catch {
          // 保留旧哈希；覆盖时会重新读取并核对。
        }
        this.store.set({ conflictOpen: true });
        return;
      }
      const message =
        error instanceof ApiError && error.issues.length > 0
          ? error.issues.map((issue) => issue.message).join("；")
          : errorMessage(error, "保存失败");
      this.store.set({ status: { kind: "error", message } });
      toast.error(`${request.path} 未保存：${message}`);
    }
  }

  private async afterConfigFileSaved(): Promise<void> {
    const before = config.state.phase;
    if (before === "broken" || before === "failed") {
      await config.load();
      const after: string = config.state.phase;
      if (after === "ready") toast.success("mybot.toml 已修复，配置页恢复可用");
      return;
    }
    await config.refreshIfClean();
  }

  async resolveConflict(choice: "overwrite" | "discard" | "keep"): Promise<void> {
    const open = this.state.open;
    if (!open) return;
    if (choice === "overwrite") {
      this.blockedVersion = null;
      this.store.set({ conflictOpen: false });
      await this.save(true);
      return;
    }
    if (choice === "discard") {
      this.store.set({ conflictOpen: false });
      await this.load(open.path);
      return;
    }
    this.store.set({ conflictOpen: false, status: { kind: "hold" } });
  }

  async copyLocal(): Promise<void> {
    const open = this.state.open;
    if (!open) return;
    try {
      await navigator.clipboard.writeText(open.content);
      toast.success("本地内容已复制到剪贴板");
    } catch {
      toast.error("复制失败，请在编辑器里全选后手动复制");
    }
  }

  /** Ctrl S */
  async saveNow(): Promise<void> {
    const state = this.state;
    if (!state.open) return;
    if (state.conflictOpen) return;
    if (!this.isDirty()) {
      toast.info("当前文件没有未保存的修改");
      return;
    }
    this.blockedVersion = null;
    await this.save();
  }

  async flush(): Promise<boolean> {
    if (this.timer !== 0) {
      this.blockedVersion = null;
      await this.save();
    }
    if (this.inflight) await this.inflight;
    return !this.isDirty();
  }

  /** 新建空文件并打开；路径相对 config/。 */
  async create(rawPath: string): Promise<string | null> {
    const path = rawPath.trim().replace(/^\.\//, "");
    if (path === "") return "请输入文件路径";
    if (path.includes("\\")) return "请使用正斜杠 / 分隔目录";
    if (path.startsWith("/") || /^[A-Za-z]:/.test(path)) return "请填写相对 config/ 的路径";
    if (path.split("/").some((part) => part === "" || part === "." || part === "..")) {
      return "路径里不能有空目录名、. 或 ..";
    }
    if (this.state.list?.includes(path)) return "这个文件已经存在";
    try {
      await api.saveFile(path, "", null);
    } catch (error) {
      if (error instanceof ApiError && error.issues.length > 0) {
        return error.issues.map((issue) => issue.message).join("；");
      }
      return errorMessage(error, "新建失败");
    }
    await this.refreshList();
    toast.success(`已新建 ${path}`);
    if (!this.isDirty() && !this.inflight) void this.load(path);
    return null;
  }
}

export const files = new FilesController();

export function useFiles<T>(selector: (state: FilesState) => T, isEqual?: (a: T, b: T) => boolean): T {
  return useStore(files.store, selector, isEqual);
}
