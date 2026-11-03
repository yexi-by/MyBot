import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";
export const themeLabels: Record<Theme, string> = {
  system: "跟随系统",
  light: "浅色",
  dark: "深色",
};

export type Skin = "clean" | "gradient";
export const skinLabels: Record<Skin, string> = {
  clean: "纯净默认",
  gradient: "清透微渐变",
};

const media = window.matchMedia("(prefers-color-scheme: dark)");
const listeners = new Set<() => void>();
let theme = read();
let skin = readSkin();

// 与 index.html 首帧脚本使用同一个键名和取值。
function read(): Theme {
  try {
    const value = localStorage.getItem("theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}
function readSkin(): Skin {
  try {
    return localStorage.getItem("skin") === "gradient" ? "gradient" : "clean";
  } catch {
    return "clean";
  }
}
function apply() {
  document.documentElement.classList.toggle(
    "dark",
    theme === "dark" || (theme === "system" && media.matches),
  );
  document.documentElement.classList.toggle("skin-gradient", skin === "gradient");
}
export function setTheme(next: Theme) {
  theme = next;
  try {
    if (next === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", next);
  } catch {
    /* 存储被浏览器禁用时仍允许本次切换。 */
  }
  apply();
  listeners.forEach((listener) => listener());
}
export function setSkin(next: Skin) {
  skin = next;
  try {
    if (next === "clean") localStorage.removeItem("skin");
    else localStorage.setItem("skin", next);
  } catch {
    /* 存储被浏览器禁用时仍允许本次切换。 */
  }
  apply();
  listeners.forEach((listener) => listener());
}
export function cycleTheme() {
  setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system");
}
export function useTheme(): Theme {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => theme,
  );
}
export function useSkin(): Skin {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => skin,
  );
}
media.addEventListener("change", () => {
  if (theme === "system") apply();
});
apply();
