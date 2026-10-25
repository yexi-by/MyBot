/**
 * 字段网格补位：某一行放不满（末行，或下一个宽字段换行留下的缺口）时，
 * 把这一行最后一个字段拉宽到行尾，任何列数、筛选状态下都不留空格子。
 *
 * 列数取实际渲染结果（容器查询决定），字段本身的跨列按类名推算，与 page.css 的规则保持一致：
 * fld--span3 占满一行；fld--span2 占两列；fld--wide 只在三列网格里占两列。
 */

import { useLayoutEffect, type RefObject } from "react";

function baseSpan(el: HTMLElement, cols: number, twoColSection: boolean): number {
  const list = el.classList;
  if (list.contains("fld--span3")) return cols;
  if (list.contains("fld--span2")) return Math.min(2, cols);
  if (list.contains("fld--wide") && cols >= 3 && !twoColSection) return 2;
  return 1;
}

function fill(body: HTMLElement) {
  // 旧补位跨列会在容器缩窄后生成隐式列，必须先清掉再读取真实列数。
  for (const el of body.children) el.removeAttribute("data-fill");
  const cols = getComputedStyle(body).gridTemplateColumns.split(" ").filter(Boolean).length;
  const twoColSection = body.parentElement?.classList.contains("sec--cols2") ?? false;
  const items = [...body.children].filter(
    (el): el is HTMLElement => el instanceof HTMLElement && el.hasAttribute("data-field") && !el.hasAttribute("data-filtered"),
  );
  const wanted = new Map<HTMLElement, number>();
  let cursor = 0;
  let last: HTMLElement | null = null;
  let lastSpan = 0;
  const close = () => {
    if (last && cursor > 0 && cursor < cols) wanted.set(last, lastSpan + cols - cursor);
    cursor = 0;
    last = null;
  };
  if (cols > 1) {
    for (const el of items) {
      const span = baseSpan(el, cols, twoColSection);
      if (cursor + span > cols) close();
      cursor += span;
      last = el;
      lastSpan = span;
      if (cursor === cols) close();
    }
    close();
  }
  for (const [el, span] of wanted) el.setAttribute("data-fill", String(span));
}

/** 在字段网格容器上启用补位；尺寸变化、字段增删和字段筛选后都会重算。 */
export function useGridFill(ref: RefObject<HTMLElement | null>, enabled = true) {
  useLayoutEffect(() => {
    const body = ref.current;
    if (!body || !enabled) return;
    const run = () => fill(body);
    run();
    const resize = new ResizeObserver(run);
    resize.observe(body);
    const mutation = new MutationObserver(run);
    mutation.observe(body, { childList: true, subtree: true, attributeFilter: ["data-filtered"] });
    return () => {
      resize.disconnect();
      mutation.disconnect();
    };
  }, [ref, enabled]);
}
