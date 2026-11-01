export function formatNumber(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.round(value).toLocaleString("zh-CN")
    : "—";
}

const decimal = ["B", "KB", "MB", "GB", "TB"];
const binary = ["B", "KiB", "MiB", "GiB", "TiB"];
export function formatBytes(
  value: number | null | undefined,
  family: "decimal" | "binary" = "decimal",
): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const base = family === "binary" ? 1024 : 1000;
  const units = family === "binary" ? binary : decimal;
  let size = value,
    index = 0;
  while (Math.abs(size) >= base && index < units.length - 1) {
    size /= base;
    index++;
  }
  const digits = index === 0 || size >= 100 ? 0 : size >= 10 ? 1 : 2;
  return (
    Number(size.toFixed(digits)).toLocaleString("zh-CN") + " " + units[index]
  );
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return Math.floor(seconds) + " 秒";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + " 分钟";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + " 小时 " + (minutes % 60) + " 分";
  return Math.floor(hours / 24) + " 天 " + (hours % 24) + " 小时";
}

export function formatClock(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString("zh-CN", {
    hour12: false,
  });
}

/** 本地日期键，用于把时间线按自然日分组。 */
export function dayKey(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toDateString();
}

/** 时间线的日期分隔标题：今天、昨天或具体日期。 */
export function formatDay(epochSeconds: number, now = Date.now()): string {
  const date = new Date(epochSeconds * 1000);
  const start = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((start(new Date(now)) - start(date)) / 86_400_000);
  const text = date.toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "short" });
  return days === 0 ? "今天 · " + text : days === 1 ? "昨天 · " + text : text;
}

export function formatRelative(epochSeconds: number, now = Date.now()): string {
  const delta = Math.max(0, now / 1000 - epochSeconds);
  if (delta < 10) return "刚刚";
  if (delta < 60) return Math.floor(delta) + " 秒前";
  if (delta < 3600) return Math.floor(delta / 60) + " 分钟前";
  const date = new Date(epochSeconds * 1000);
  if (delta < 86400) return formatClock(epochSeconds);
  return date.toLocaleString("zh-CN", { hour12: false });
}

export interface ScopeKey {
  bot: string;
  kind: "group" | "private";
  peer: string;
}
/** 运行时会话键形如 `机器人:group:群号`。 */
export function parseScopeKey(key: string): ScopeKey | null {
  const [bot, kind, ...rest] = key.split(":");
  if (!bot || (kind !== "group" && kind !== "private") || !rest.length)
    return null;
  return { bot, kind, peer: rest.join(":") };
}

export function plural(count: number, unit: string) {
  return formatNumber(count) + " " + unit;
}
