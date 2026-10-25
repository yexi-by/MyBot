import { transition } from "./navigation";
export type Theme = "light" | "dark" | "system";
const media = window.matchMedia("(prefers-color-scheme: dark)");
export function readTheme(): Theme {
  try {
    const value = localStorage.getItem("theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}
function apply(theme: Theme) {
  document.documentElement.classList.toggle(
    "dark",
    theme === "dark" || (theme === "system" && media.matches),
  );
}
export function setTheme(theme: Theme) {
  try {
    localStorage.setItem("theme", theme);
  } catch {
    /* 存储被浏览器禁用时仍允许本次切换。 */
  }
  transition(() => apply(theme));
}
media.addEventListener("change", () => {
  if (readTheme() === "system") apply("system");
});
