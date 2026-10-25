/** 本页字段筛选：按 data-search 文本隐藏不匹配的字段、群卡片和分节，DOM 新增节点时自动重算。 */

import { useLayoutEffect, useState, type RefObject } from "react";

function setFiltered(element: Element, filtered: boolean): void {
  if (filtered) element.setAttribute("data-filtered", "");
  else element.removeAttribute("data-filtered");
}

function apply(root: HTMLElement, query: string): number {
  const needle = query.trim().toLowerCase();
  let visibleSections = 0;
  for (const section of root.querySelectorAll<HTMLElement>("[data-section]")) {
    const sectionHit = needle === "" || (section.dataset.search ?? "").includes(needle);
    let anyField = false;
    for (const field of section.querySelectorAll<HTMLElement>("[data-field]")) {
      const hit = sectionHit || needle === "" || (field.dataset.search ?? "").includes(needle);
      setFiltered(field, !hit);
      if (hit) anyField = true;
    }
    for (const card of section.querySelectorAll<HTMLElement>("[data-filter-card]")) {
      const cardHit =
        sectionHit ||
        needle === "" ||
        (card.dataset.search ?? "").includes(needle) ||
        card.querySelector("[data-field]:not([data-filtered])") !== null;
      setFiltered(card, !cardHit);
      if (cardHit && (card.dataset.search ?? "").includes(needle)) {
        for (const field of card.querySelectorAll("[data-field]")) setFiltered(field, false);
      }
    }
    const visible = sectionHit || anyField;
    setFiltered(section, !visible);
    if (visible) visibleSections += 1;
  }
  for (const row of root.querySelectorAll<HTMLElement>(".sec-row")) {
    const children = [...row.children];
    setFiltered(row, children.length > 0 && children.every((child) => child.hasAttribute("data-filtered")));
  }
  return visibleSections;
}

/** 返回 true 表示有筛选词但一个都没匹配到。 */
export function useFieldFilter(root: RefObject<HTMLElement | null>, query: string): boolean {
  const [empty, setEmpty] = useState(false);
  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const run = () => setEmpty(apply(element, query) === 0 && query.trim() !== "");
    run();
    if (query.trim() === "") return;
    const observer = new MutationObserver(run);
    observer.observe(element, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [root, query]);
  return empty;
}
