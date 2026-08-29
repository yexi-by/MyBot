/** 页面卡片包装：标题 + 描述 + 操作区 + 内容；className 用于在棋盘网格中跨列。 */

import type { ReactNode } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface SectionCardProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  /** 卡片内字段网格列数：宽卡（跨列）用 3 提高密度。 */
  cols?: 2 | 3;
  children: ReactNode;
}

export function SectionCard({
  title,
  description,
  actions,
  className,
  cols = 2,
  children,
}: SectionCardProps) {
  return (
    <Card
      data-section={title}
      className={cn(
        "card-geek [--card-spacing:--spacing(3)] transition-shadow hover:ring-foreground/25",
        className,
      )}
    >
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle className="font-mono tracking-tight">{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {actions}
      </CardHeader>
      <CardContent
        className={cn(
          "grid grid-cols-1 gap-3",
          cols === 2 ? "xl:grid-cols-2" : "xl:grid-cols-3",
        )}
      >
        {children}
      </CardContent>
    </Card>
  );
}
