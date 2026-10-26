import { useSyncExternalStore } from "react";

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger";

export interface ConfirmRequest {
  kind: "confirm";
  title: string;
  message: string;
  confirmLabel: string;
  danger: boolean;
  resolve: (value: boolean) => void;
}
export interface PromptRequest {
  kind: "prompt";
  title: string;
  message: string;
  label: string;
  placeholder: string;
  initial: string;
  confirmLabel: string;
  validate: (value: string) => string;
  resolve: (value: string | null) => void;
}
export interface Toast {
  id: number;
  tone: "success" | "danger" | "neutral";
  message: string;
}
interface UiState {
  dialog: ConfirmRequest | PromptRequest | null;
  toasts: Toast[];
  palette: boolean;
  /** 配置冲突对话框；选择“继续编辑”后关闭，冲突状态仍保留。 */
  conflict: boolean;
  /** 窄屏下的导航抽屉。 */
  nav: boolean;
}

let state: UiState = { dialog: null, toasts: [], palette: false, conflict: false, nav: false };
const listeners = new Set<() => void>();
function update(next: Partial<UiState>) {
  state = { ...state, ...next };
  listeners.forEach((listener) => listener());
}
export function useUi(): UiState {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => state,
  );
}

function closePending() {
  const pending = state.dialog;
  if (pending?.kind === "confirm") pending.resolve(false);
  else if (pending?.kind === "prompt") pending.resolve(null);
}

export function confirm(options: {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
}): Promise<boolean> {
  closePending();
  return new Promise((resolve) =>
    update({
      dialog: { kind: "confirm", danger: false, ...options, resolve },
    }),
  );
}

export function prompt(options: {
  title: string;
  message?: string;
  label: string;
  placeholder?: string;
  initial?: string;
  confirmLabel: string;
  validate?: (value: string) => string;
}): Promise<string | null> {
  closePending();
  return new Promise((resolve) =>
    update({
      dialog: {
        kind: "prompt",
        message: "",
        placeholder: "",
        initial: "",
        validate: () => "",
        ...options,
        resolve,
      },
    }),
  );
}

export function settleDialog(value: boolean | string | null) {
  const pending = state.dialog;
  if (!pending) return;
  update({ dialog: null });
  if (pending.kind === "confirm") pending.resolve(value === true);
  else pending.resolve(typeof value === "string" ? value : null);
}

let toastId = 0;
export function toast(message: string, tone: Toast["tone"] = "neutral") {
  const id = ++toastId;
  update({ toasts: [...state.toasts, { id, tone, message }].slice(-4) });
  setTimeout(() => dismissToast(id), tone === "danger" ? 8000 : 4000);
}
export function dismissToast(id: number) {
  if (state.toasts.some((item) => item.id === id))
    update({ toasts: state.toasts.filter((item) => item.id !== id) });
}

export function setPalette(open: boolean) {
  update({ palette: open });
}
export function setConflictDialog(open: boolean) {
  update({ conflict: open });
}
export function setNav(open: boolean) {
  update({ nav: open });
}

// 当前页面上处理 Ctrl+S 的保存动作，例如正在编辑的文本文件；为空时保存配置。
let saver: (() => void) | null = null;
export function setActiveSaver(next: (() => void) | null) {
  saver = next;
}
export function activeSaver() {
  return saver;
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (callback) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", callback);
      return () => media.removeEventListener("change", callback);
    },
    () => window.matchMedia(query).matches,
  );
}
