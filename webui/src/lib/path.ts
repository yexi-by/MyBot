/** 配置路径工具：路径是分段数组，provider ID 等动态键可以含点号，不能用点号拼接。 */

import type { Path, PathSeg } from "./types";

export function pathKey(path: Path): string {
  return JSON.stringify(path);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function getIn(root: unknown, path: Path): unknown {
  let current: unknown = root;
  for (const seg of path) {
    if (typeof seg === "number") {
      if (!Array.isArray(current)) return undefined;
      current = current[seg];
    } else {
      if (!isRecord(current) || !Object.hasOwn(current, seg)) return undefined;
      current = current[seg];
    }
  }
  return current;
}

/**
 * 不可变写入，沿途只复制被改动的容器。
 * value 为 null / undefined 时删除对象键，与后端 exclude_none 的导出形态保持一致。
 */
export function setIn<T>(root: T, path: Path, value: unknown): T {
  if (path.length === 0) return value as T;
  if (getIn(root, path) === value) return root;
  if ((value === null || value === undefined) && getIn(root, path) === undefined) return root;
  const [head, ...rest] = path;
  if (typeof head === "number") {
    const list: unknown[] = Array.isArray(root) ? [...(root as unknown[])] : [];
    list[head] = rest.length === 0 ? value : setIn(list[head] ?? containerFor(rest[0]), rest, value);
    return list as T;
  }
  const record: Record<string, unknown> = isRecord(root) ? { ...root } : {};
  if (rest.length === 0) {
    if (value === null || value === undefined) delete record[head];
    else record[head] = value;
    return record as T;
  }
  record[head] = setIn(record[head] ?? containerFor(rest[0]), rest, value);
  return record as T;
}

function containerFor(next: PathSeg | undefined): unknown {
  return typeof next === "number" ? [] : {};
}

/** 结构相等；null 与缺失视为相同（后端导出时省略 None）。 */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || a === undefined || b === null || b === undefined) {
    return (a === null || a === undefined) && (b === null || b === undefined);
  }
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, index) => deepEqual(item, b[index]));
  }
  if (isRecord(a)) {
    if (!isRecord(b)) return false;
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (!deepEqual(a[key], b[key])) return false;
    }
    return true;
  }
  return false;
}

export function startsWith(path: Path, prefix: Path): boolean {
  if (prefix.length > path.length) return false;
  return prefix.every((seg, index) => path[index] === seg);
}

export interface Leaf {
  path: PathSeg[];
  before: unknown;
  after: unknown;
}

/**
 * 列出两份配置之间的最小改动单元。
 * 表格数组（如 [[groups]]）按下标逐项比较；纯值数组、整段新增或删除的表算作一处改动。
 */
export function diffLeaves(before: unknown, after: unknown, path: PathSeg[] = [], out: Leaf[] = []): Leaf[] {
  if (deepEqual(before, after)) return out;
  if (isRecord(before) && isRecord(after)) {
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
      diffLeaves(before[key], after[key], [...path, key], out);
    }
    return out;
  }
  if (
    Array.isArray(before) &&
    Array.isArray(after) &&
    (before.some(isRecord) || after.some(isRecord))
  ) {
    const length = Math.max(before.length, after.length);
    for (let index = 0; index < length; index += 1) {
      diffLeaves(before[index], after[index], [...path, index], out);
    }
    return out;
  }
  out.push({ path, before, after });
  return out;
}

/** TOML 表头里的键：裸键原样输出，含点号等字符时加引号。 */
export function tomlKey(seg: PathSeg): string {
  const text = String(seg);
  return /^[A-Za-z0-9_-]+$/.test(text) ? text : JSON.stringify(text);
}

export function tomlPath(path: Path): string {
  return path.filter((seg) => typeof seg === "string").map(tomlKey).join(".");
}
