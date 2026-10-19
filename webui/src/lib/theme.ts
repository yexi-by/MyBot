/** 亮 / 暗主题：localStorage.theme 取 light / dark / system，与 index.html 的预初始化脚本一致。 */

import { Store, useStore } from "./store";
import { withTransition } from "./transition";

export type ThemeChoice = "light" | "dark" | "system";

const KEY = "theme";
const media = window.matchMedia("(prefers-color-scheme: dark)");

function readChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // 无法访问 localStorage 时跟随系统。
  }
  return "system";
}

function resolve(choice: ThemeChoice): boolean {
  return choice === "dark" || (choice === "system" && media.matches);
}

export const themeStore = new Store<{ choice: ThemeChoice; dark: boolean }>({
  choice: readChoice(),
  dark: document.documentElement.classList.contains("dark"),
});

function paint(dark: boolean): void {
  if (document.documentElement.classList.contains("dark") === dark) return;
  withTransition(() => {
    document.documentElement.classList.toggle("dark", dark);
    themeStore.set({ dark });
  });
}

export function setTheme(choice: ThemeChoice): void {
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    // 忽略存储失败，本次会话仍然生效。
  }
  themeStore.set({ choice });
  paint(resolve(choice));
}

export function toggleTheme(): void {
  setTheme(themeStore.get().dark ? "light" : "dark");
}

media.addEventListener("change", () => {
  if (themeStore.get().choice === "system") paint(media.matches);
});

export function useTheme() {
  return useStore(themeStore, (state) => state);
}
