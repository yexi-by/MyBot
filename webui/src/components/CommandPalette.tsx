/** Ctrl K 命令面板：搜任意字段（预览当前值，回车跳转并闪烁定位）、页面、config/ 文件与操作。 */

import {
  CheckCheck,
  FileText,
  History,
  Monitor,
  Power,
  RotateCw,
  Save,
  Search,
  SlidersHorizontal,
  SunMoon,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { config } from "@/lib/config";
import { CONFIG_FILE, files } from "@/lib/files";
import { cls, NAV_ITEMS } from "@/lib/pages";
import { deepEqual, getIn } from "@/lib/path";
import { requestPower } from "@/lib/power";
import { navigate, routerStore, useRoute } from "@/lib/router";
import { setTheme, toggleTheme } from "@/lib/theme";
import { closePalette, useUi } from "@/lib/ui";
import { formatValue } from "@/schema";
import { buildFieldIndex, type FieldEntry } from "@/schema/search";

import { Dialog } from "./Dialog";

interface Item {
  id: string;
  group: "字段" | "页面" | "文件" | "操作";
  label: string;
  sub: string;
  icon: LucideIcon;
  haystack: string;
  run: () => void;
  field?: FieldEntry;
  hint?: string;
}

function matches(haystack: string, tokens: string[]): number {
  let score = 0;
  for (const token of tokens) {
    const index = haystack.indexOf(token);
    if (index < 0) return -1;
    score += index === 0 ? 3 : 1;
  }
  return score;
}

function highlight(text: string, tokens: string[]): ReactNode {
  const token = tokens.find((item) => text.toLowerCase().includes(item));
  if (!token) return text;
  const index = text.toLowerCase().indexOf(token);
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + token.length)}</mark>
      {text.slice(index + token.length)}
    </>
  );
}

function buildItems(): Item[] {
  const draft = config.state.draft;
  const items: Item[] = [];
  for (const nav of NAV_ITEMS) {
    items.push({
      id: `page:${nav.key}`,
      group: "页面",
      label: nav.label,
      sub: nav.table,
      icon: nav.icon,
      haystack: `${nav.label} ${nav.table} ${nav.group}`.toLowerCase(),
      run: () => navigate({ page: nav.key }),
      hint: `打开「${nav.label}」页`,
    });
  }
  const actions: [string, string, LucideIcon, () => void, string][] = [
    ["save", "立即保存", Save, () => void (routerStore.get().route.page === "files" ? files.saveNow() : config.saveNow()), "跳过等待，马上写入当前页的修改（Ctrl S）"],
    ["validate", "校验配置", CheckCheck, () => void config.validateNow(), "不写入，只用后端规则完整校验当前表单"],
    ["theme", "切换亮暗主题", SunMoon, toggleTheme, "在亮色和暗色之间切换"],
    ["theme-system", "主题跟随系统", Monitor, () => setTheme("system"), "按操作系统的亮暗设置自动切换"],
    ["raw", "打开 mybot.toml 原文", FileText, () => navigate({ page: "files", file: CONFIG_FILE }), "在配置文本里直接编辑原文件"],
    ["log", "查看写回记录", History, () => document.querySelector<HTMLButtonElement>(".st")?.click(), "本次会话每次写入改了什么"],
    ["restart", "重启 MyBot", RotateCw, () => requestPower("restart"), "写完待保存的修改后优雅重启进程"],
    ["shutdown", "关闭 MyBot", Power, () => requestPower("shutdown"), "写完待保存的修改后让进程优雅退出"],
  ];
  for (const [id, label, icon, run, hint] of actions) {
    items.push({ id: `action:${id}`, group: "操作", label, sub: "", icon, haystack: `${label} ${id}`.toLowerCase(), run, hint });
  }
  for (const path of files.state.list ?? []) {
    items.push({
      id: `file:${path}`,
      group: "文件",
      label: path.split("/").pop() ?? path,
      sub: `config/${path}`,
      icon: FileText,
      haystack: path.toLowerCase(),
      run: () => navigate({ page: "files", file: path }),
      hint: "在配置文本里打开",
    });
  }
  for (const entry of buildFieldIndex(draft)) {
    items.push({
      id: `field:${JSON.stringify(entry.path)}`,
      group: "字段",
      label: entry.label,
      sub: `${entry.scope} · ${entry.field.key}`,
      icon: SlidersHorizontal,
      haystack: entry.haystack,
      run: () => navigate({ page: entry.page }, { reveal: entry.path }),
      field: entry,
    });
  }
  return items;
}

const GROUP_ORDER: Item["group"][] = ["字段", "页面", "文件", "操作"];
const LIMITS: Record<Item["group"], number> = { 字段: 40, 页面: 10, 文件: 12, 操作: 10 };

function Preview({ item }: { item: Item | undefined }) {
  if (!item) return <p className="pv-desc">用方向键选择，回车执行。</p>;
  if (!item.field) {
    return (
      <>
        <p className="pv-path">{item.sub || item.group}</p>
        <p className="pv-title">{item.label}</p>
        {item.hint ? <p className="pv-desc">{item.hint}</p> : null}
      </>
    );
  }
  const { field, path, toml, scope } = item.field;
  const state = config.state;
  const value = getIn(state.draft, path);
  const saved = getIn(state.saved, path);
  const dirty = !deepEqual(value, saved);
  return (
    <>
      <p className="pv-path">{toml}</p>
      <p className="pv-title">{field.label}</p>
      <p className="pv-desc">{field.desc ?? scope}</p>
      <div className="pv-value">
        当前值{dirty ? <span className="pv-dirty">（未写入）</span> : null}
        <code>{formatValue(value, field)}</code>
      </div>
      {dirty ? (
        <div className="pv-value">
          文件中的值
          <code>{formatValue(saved, field)}</code>
        </div>
      ) : null}
    </>
  );
}

export function CommandPalette() {
  const open = useUi((state) => state.palette);
  const route = useRoute();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [items, setItems] = useState<Item[]>([]);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setItems(buildItems());
    setQuery("");
    setActive(0);
    if (files.state.list === null) void files.refreshList().then(() => setItems(buildItems()));
  }, [open, route.page]);

  const groups = useMemo(() => {
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const result: { group: Item["group"]; items: Item[] }[] = [];
    for (const group of GROUP_ORDER) {
      let groupItems = items.filter((item) => item.group === group);
      if (tokens.length === 0) {
        if (group === "字段" || group === "文件") continue;
      } else {
        groupItems = groupItems
          .map((item) => ({ item, score: matches(item.haystack, tokens) + (item.label.toLowerCase().includes(tokens[0] ?? "") ? 4 : 0) }))
          .filter(({ score }) => score >= 0)
          .sort((a, b) => b.score - a.score)
          .map(({ item }) => item);
      }
      if (groupItems.length > 0) result.push({ group, items: groupItems.slice(0, LIMITS[group]) });
    }
    return result;
  }, [items, query]);

  const flat = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const current = flat[Math.min(active, flat.length - 1)];

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const run = (item: Item | undefined) => {
    if (!item) return;
    closePalette();
    // 等对话框关闭、焦点归还后再执行，避免跳转后焦点被抢回。
    requestAnimationFrame(() => item.run());
  };

  let index = -1;
  return (
    <Dialog open={open} onClose={closePalette} className="pal" labelledBy="palette-label">
      <span className="sr-only" id="palette-label">
        命令面板
      </span>
      <div className="pal-input">
        <Search className="ic" aria-hidden />
        <input
          autoFocus
          value={query}
          placeholder="搜索字段、页面、文件或操作…"
          aria-label="搜索"
          aria-controls="palette-list"
          aria-activedescendant={current ? `pal-${active}` : undefined}
          role="combobox"
          aria-expanded="true"
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive((value) => Math.min(flat.length - 1, value + 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((value) => Math.max(0, value - 1));
            } else if (event.key === "Enter") {
              event.preventDefault();
              run(current);
            }
          }}
        />
        <kbd>Esc</kbd>
      </div>
      <div className="pal-body">
        <div className="pal-list" id="palette-list" role="listbox" ref={list} aria-label="搜索结果">
          {flat.length === 0 ? (
            <p className="pal-empty">没有匹配「{query}」的字段、页面或操作。</p>
          ) : (
            groups.map((group) => (
              <div key={group.group} role="group" aria-label={group.group}>
                <p className="pal-group">{group.group}</p>
                {group.items.map((item) => {
                  index += 1;
                  const itemIndex = index;
                  const Icon = item.icon;
                  return (
                    <button
                      type="button"
                      key={item.id}
                      id={`pal-${itemIndex}`}
                      role="option"
                      aria-selected={itemIndex === active}
                      data-index={itemIndex}
                      tabIndex={-1}
                      className={cls("pal-item", itemIndex === active && "is-active")}
                      onMouseMove={() => {
                        if (active !== itemIndex) setActive(itemIndex);
                      }}
                      onClick={() => run(item)}
                    >
                      <span className="pal-ic">
                        <Icon className="ic" aria-hidden />
                      </span>
                      <span className="pal-main">
                        <span className="pal-label">{highlight(item.label, tokens)}</span>
                        {item.sub ? <span className="pal-sub">{item.sub}</span> : null}
                      </span>
                      {item.field ? <span className="pal-meta">{NAV_ITEMS.find((nav) => nav.key === item.field?.page)?.label}</span> : null}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
        <aside className="pal-preview" aria-live="polite">
          <Preview item={current} />
        </aside>
      </div>
      <div className="pal-foot">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd>选择
        </span>
        <span>
          <kbd>Enter</kbd>执行 / 跳转
        </span>
        <span>
          <kbd>/</kbd>只筛选本页
        </span>
      </div>
    </Dialog>
  );
}
