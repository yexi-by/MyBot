/** 设置块棋盘网格：卡片从左到右排列，超出自动换到下一行。 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface SettingsGridProps {
  children: ReactNode;
  className?: string;
}

export function SettingsGrid({ children, className }: SettingsGridProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 items-start gap-4 md:grid-cols-2 2xl:grid-cols-3",
        className,
      )}
    >
      {children}
    </div>
  );
}
