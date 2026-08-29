/** 页内字段筛选：按查询串隐藏不匹配的 SectionCard（data-section 元素）。 */

import { useEffect, useRef } from "react";

/**
 * 返回容器 ref；query 非空时隐藏 textContent 不匹配的 data-section 卡片。
 * 每次渲染都同步，保证动态增删的卡片（如新增群配置）也被正确处理。
 */
export function useFieldFilter<T extends HTMLElement = HTMLDivElement>(
  query: string,
) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const normalized = query.trim().toLowerCase();
    const sections = root.querySelectorAll<HTMLElement>("[data-section]");
    for (const el of sections) {
      const matches =
        normalized === "" ||
        (el.textContent ?? "").toLowerCase().includes(normalized);
      el.classList.toggle("hidden", !matches);
    }
    return () => {
      for (const el of sections) el.classList.remove("hidden");
    };
  });
  return ref;
}
