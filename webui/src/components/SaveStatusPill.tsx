/** 自动保存状态丸：把多种保存状态收敛为彩色圆点 + 短文案，失败时附重试。 */

import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type SaveStatusKind =
  | "idle"
  | "editing"
  | "saving"
  | "watching"
  | "applied"
  | "saved"
  | "invalid"
  | "conflict"
  | "error";

const STATUS_META: Record<
  SaveStatusKind,
  { label: string; dot: string; pulse: boolean }
> = {
  idle: { label: "自动保存", dot: "bg-muted-foreground/50", pulse: false },
  editing: { label: "待保存", dot: "bg-primary", pulse: true },
  saving: { label: "保存中", dot: "bg-primary", pulse: true },
  watching: { label: "热生效中", dot: "bg-primary", pulse: true },
  applied: { label: "已热生效", dot: "bg-green-500", pulse: false },
  saved: { label: "已保存", dot: "bg-green-500", pulse: false },
  invalid: { label: "内容有误", dot: "bg-destructive", pulse: false },
  conflict: { label: "保存冲突", dot: "bg-destructive", pulse: false },
  error: { label: "保存失败", dot: "bg-destructive", pulse: false },
};

interface SaveStatusPillProps {
  state: SaveStatusKind;
  onRetry?: () => void;
}

export function SaveStatusPill({ state, onRetry }: SaveStatusPillProps) {
  const meta = STATUS_META[state];
  return (
    <span className="inline-flex h-7 items-center gap-1.5 rounded-sm border bg-background px-2.5 font-mono text-[11px] tracking-wide text-muted-foreground">
      <span className="relative flex size-2">
        {meta.pulse ? (
          <span
            className={cn(
              "absolute inline-flex size-full animate-ping opacity-60",
              meta.dot,
            )}
            aria-hidden
          />
        ) : null}
        <span
          className={cn("relative inline-flex size-2", meta.dot)}
          aria-hidden
        />
      </span>
      {meta.label}
      {(state === "error" || state === "conflict") && onRetry ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={state === "conflict" ? "处理保存冲突" : "重试保存"}
          onClick={onRetry}
        >
          <RotateCw className="size-3" />
        </Button>
      ) : null}
    </span>
  );
}
