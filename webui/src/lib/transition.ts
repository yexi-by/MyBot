/** View Transitions 包装：不支持或用户偏好减少动效时直接执行；after 在新 DOM 提交后运行。 */

import { flushSync } from "react-dom";

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

export function withTransition(update: () => void, after?: () => void): void {
  const run = () => {
    flushSync(update);
    after?.();
  };
  if (reducedMotion.matches || typeof document.startViewTransition !== "function" || document.hidden) {
    run();
    return;
  }
  document.startViewTransition(run);
}
