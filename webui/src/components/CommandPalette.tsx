/** Ctrl+K 命令面板：页面跳转与常用动作的模糊搜索入口（自实现，无新依赖）。 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { Search } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface PaletteItem {
  id: string;
  group: string;
  label: string;
  keywords?: string;
  icon: LucideIcon;
  run: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: PaletteItem[];
}

export function CommandPalette({
  open,
  onOpenChange,
  items,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
    }
  }, [open]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (normalized === "") return items;
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(normalized) ||
        (item.keywords ?? "").toLowerCase().includes(normalized),
    );
  }, [items, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-index="${activeIndex}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const runItem = (item: PaletteItem) => {
    onOpenChange(false);
    item.run();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) =>
        filtered.length === 0 ? 0 : (current + 1) % filtered.length,
      );
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) =>
        filtered.length === 0
          ? 0
          : (current - 1 + filtered.length) % filtered.length,
      );
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = filtered[activeIndex];
      if (item) runItem(item);
    }
  };

  const groups = useMemo(() => {
    const result: { title: string; entries: { item: PaletteItem; index: number }[] }[] = [];
    filtered.forEach((item, index) => {
      let group = result.find((entry) => entry.title === item.group);
      if (!group) {
        group = { title: item.group, entries: [] };
        result.push(group);
      }
      group.entries.push({ item, index });
    });
    return result;
  }, [filtered]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="top-[20%] translate-y-0 gap-3 p-3 sm:max-w-md">
        <DialogTitle className="sr-only">命令面板</DialogTitle>
        <div className="relative">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="搜索页面或操作…"
            aria-label="搜索页面或操作"
            className="pl-8"
          />
        </div>
        <div ref={listRef} className="max-h-72 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              没有匹配的页面或操作
            </p>
          ) : (
            groups.map((group) => (
              <div key={group.title} className="px-1 py-1">
                <p className="px-2 pb-1 text-xs font-medium text-muted-foreground">
                  {group.title}
                </p>
                {group.entries.map(({ item, index }) => (
                  <button
                    key={item.id}
                    type="button"
                    data-index={index}
                    onClick={() => runItem(item)}
                    onMouseEnter={() => setActiveIndex(index)}
                    className={cn(
                      "flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                      index === activeIndex
                        ? "bg-accent text-accent-foreground"
                        : "hover:bg-accent/60",
                    )}
                  >
                    <item.icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    {item.label}
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
