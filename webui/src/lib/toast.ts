/** 底部居中的轻提示；同一时刻最多保留三条。 */

import { Store } from "./store";

export type ToastTone = "info" | "success" | "error";

export interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
  action?: { label: string; run: () => void };
}

export const toastStore = new Store<{ items: ToastItem[] }>({ items: [] });

let nextId = 1;
const timers = new Map<number, number>();

export function dismissToast(id: number): void {
  window.clearTimeout(timers.get(id));
  timers.delete(id);
  toastStore.set((state) => ({ items: state.items.filter((item) => item.id !== id) }));
}

function push(tone: ToastTone, message: string, action?: ToastItem["action"]): number {
  const id = nextId++;
  toastStore.set((state) => {
    // 同文案的提示不叠加，只刷新停留时间。
    const items = state.items.filter((item) => item.message !== message);
    return { items: [...items, { id, tone, message, action }].slice(-3) };
  });
  const duration = tone === "error" ? 6000 : action ? 5000 : 3200;
  timers.set(id, window.setTimeout(() => dismissToast(id), duration));
  return id;
}

export const toast = {
  info: (message: string, action?: ToastItem["action"]) => push("info", message, action),
  success: (message: string, action?: ToastItem["action"]) => push("success", message, action),
  error: (message: string, action?: ToastItem["action"]) => push("error", message, action),
};
