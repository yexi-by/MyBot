/** 表单 dirtyFields 的读取工具：按点路径取子树、统计修改叶子数。 */

/** 按点路径取 dirtyFields 子树；不存在时返回 undefined。 */
export function valueAtPath(source: unknown, path: string): unknown {
  let current: unknown = source;
  for (const part of path.split(".")) {
    if (current === null || typeof current !== "object") {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

/** 统计 dirtyFields 子树中标记为 true 的叶子数量。 */
export function countDirtyLeaves(node: unknown): number {
  if (node === true) return 1;
  if (node === null || typeof node !== "object") return 0;
  let count = 0;
  for (const value of Object.values(node)) {
    count += countDirtyLeaves(value);
  }
  return count;
}

/** 指定前缀下是否存在任何修改。 */
export function hasDirtyUnder(dirtyFields: unknown, prefix: string): boolean {
  return countDirtyLeaves(valueAtPath(dirtyFields, prefix)) > 0;
}

/** 多个前缀下的修改叶子总数（用于页面 dirty 计数）。 */
export function countDirtyUnder(
  dirtyFields: unknown,
  prefixes: string[],
): number {
  let count = 0;
  for (const prefix of prefixes) {
    count += countDirtyLeaves(valueAtPath(dirtyFields, prefix));
  }
  return count;
}
