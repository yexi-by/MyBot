/** 页面卡片包装：标题 + 描述 + 操作区 + 内容。 */

import type { ReactNode } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface SectionCardProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function SectionCard({
  title,
  description,
  actions,
  children,
}: SectionCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1.5">
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {actions}
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {children}
      </CardContent>
    </Card>
  );
}
