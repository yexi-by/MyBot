/** 设置块棋盘网格：卡片从左到右排列，超出自动换到下一行。 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface SettingsGridProps {
  children: ReactNode;
  className?: string;
  /** 宽屏列数上限：内容少时传 2，让卡片拉宽填满行，避免空格子留白。 */
  columns?: 2 | 3;
}

export function SettingsGrid({
  children,
  className,
  columns = 3,
}: SettingsGridProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 items-start gap-3 md:grid-cols-2",
        columns === 3 && "2xl:grid-cols-3",
        className,
      )}
    >
      {children}
    </div>
  );
}
