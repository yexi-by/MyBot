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
  children: ReactNode;
}

export function SectionCard({
  title,
  description,
  actions,
  className,
  children,
}: SectionCardProps) {
  return (
    <Card className={cn(className)}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1.5">
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {actions}
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {children}
      </CardContent>
    </Card>
  );
}
