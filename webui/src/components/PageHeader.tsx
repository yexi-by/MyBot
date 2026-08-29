/** 页面标题区：标题 + 描述 + 内联警示 + dirty 计数 + 字段筛选框。 */

import { Search, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

interface PageHeaderProps {
  title: string;
  description?: string;
  /** 内联警示（如"本页改动需要重启进程后生效"），琥珀色小字。 */
  notice?: string;
  dirtyCount?: number;
  /** 提供时显示字段筛选框。 */
  filterValue?: string;
  onFilterChange?: (value: string) => void;
  actions?: ReactNode;
}

export function PageHeader({
  title,
  description,
  notice,
  dirtyCount = 0,
  filterValue,
  onFilterChange,
  actions,
}: PageHeaderProps) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2">
          <h2 className="font-mono text-lg font-semibold tracking-tight">
            {title}
          </h2>
          {dirtyCount > 0 ? (
            <Badge variant="secondary" title="本页已修改的字段数">
              {dirtyCount} 项已修改
            </Badge>
          ) : null}
        </div>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
        {notice ? (
          <p className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-500">
            <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
            {notice}
          </p>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        {actions}
        {filterValue !== undefined && onFilterChange ? (
          <div className="relative">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              value={filterValue}
              onChange={(event) => onFilterChange(event.target.value)}
              placeholder="筛选字段…"
              aria-label={`筛选${title}字段`}
              className="w-44 pl-8"
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
