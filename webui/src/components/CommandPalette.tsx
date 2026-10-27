import { useEffect, useMemo, useRef, useState } from "react";
import {
  CornerDownLeft,
  FileText,
  Hash,
  Monitor,
  Moon,
  Plug,
  RotateCw,
  Save,
  ScrollText,
  Search,
  SlidersHorizontal,
  Sparkles,
  Sun,
  User,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { save, useConfig } from "../lib/config";
import { groupTitle, scopeLabel } from "../lib/conversations";
import { turnState } from "../lib/labels";
import { navigate } from "../lib/navigation";
import { restartHarness } from "../lib/power";
import {
  filesResource,
  runtimeResource,
  toolsResource,
  useGroupNames,
  useResource,
} from "../lib/resource";
import { sessionRoute } from "../lib/sessions";
import { setTheme } from "../lib/theme";
import { object } from "../lib/types";
import type { Path } from "../lib/types";
import { setPalette, toast, useUi } from "../lib/ui";
import { focusField, navItems, settingsSections } from "../routes";
import { fieldInstances } from "../schema/fields";

interface Command {
  id: string;
  group: string;
  title: string;
  detail?: string;
  keywords?: string;
  icon: LucideIcon;
  run: () => void;
}

const go = (path: string) => () => void navigate(path);
const enc = encodeURIComponent;

function useCommands(): Command[] {
  const state = useConfig();
  const groups = useGroupNames();
  const tools = useResource(toolsResource).data;
  const files = useResource(filesResource).data;
  const sessions = useResource(runtimeResource).data?.sessions;
  return useMemo(() => {
    const config = state.draft;
    const list: Command[] = [];
    for (const item of navItems)
      list.push({ id: "page:" + item.path, group: "页面", title: item.label, icon: item.icon, run: go(item.path) });
    list.push(
      { id: "page:mcp", group: "页面", title: "MCP 服务", detail: "工具", icon: Plug, run: go("/tools/mcp") },
      { id: "page:napcat", group: "页面", title: "NapCat 覆盖清单", detail: "工具", icon: Wrench, run: go("/tools/napcat") },
      { id: "page:sandbox-settings", group: "页面", title: "沙箱设置", detail: "沙箱", icon: SlidersHorizontal, run: go("/sandbox/settings") },
    );
    for (const section of settingsSections)
      list.push({
        id: "settings:" + section.id,
        group: "页面",
        title: section.label,
        detail: "设置",
        icon: SlidersHorizontal,
        run: go("/settings/" + section.id),
      });
    list.push({
      id: "conversation:private",
      group: "会话",
      title: "私聊",
      detail: object(config.private).enabled === true ? "已启用" : "未启用",
      icon: User,
      run: go("/conversations/private"),
    });
    for (const id of Object.keys(object(config.groups)))
      list.push({
        id: "conversation:" + id,
        group: "会话",
        title: groupTitle(id, groups),
        detail: id + (object(object(config.groups)[id]).enabled === true ? " · 已启用" : " · 未启用"),
        keywords: "群 " + id,
        icon: Hash,
        run: go("/conversations/group/" + enc(id)),
      });
    for (const session of sessions ?? [])
      list.push({
        id: "session:" + session.scope_key,
        group: "会话上下文",
        title: scopeLabel(session.scope_key, groups),
        detail: session.cleared
          ? "已重置"
          : "第 " + session.turns + " 轮 · " + turnState(session.state).text,
        keywords: session.scope_key,
        icon: ScrollText,
        run: go(sessionRoute(session.scope_key)),
      });
    for (const [id, value] of Object.entries(object(config.providers)))
      list.push({
        id: "provider:" + id,
        group: "服务商",
        title: id,
        detail: String(object(value).base_url || "未填写接口地址"),
        icon: Sparkles,
        run: go("/providers/" + enc(id)),
      });
    for (const id of Object.keys(object(config.mcp)))
      list.push({ id: "mcp:" + id, group: "MCP 服务", title: id, icon: Plug, run: go("/tools/mcp/" + enc(id)) });
    for (const tool of tools?.tools ?? [])
      if (tool.category !== "harness")
        list.push({
          id: "tool:" + tool.name,
          group: "工具",
          title: tool.title,
          detail: tool.name,
          icon: Wrench,
          run: go("/tools?tool=" + enc(tool.name)),
        });
    for (const file of files ?? [])
      list.push({
        id: "file:" + file,
        group: "文件",
        title: file,
        icon: FileText,
        run: go(file.toLowerCase().endsWith(".toml") ? "/settings/config-file" : "/prompts?path=" + enc(file)),
      });
    for (const item of fieldInstances(config)) {
      const path: Path = item.path;
      list.push({
        id: "field:" + path.join("."),
        group: "设置项",
        title: item.field.label,
        detail: item.section.title + " · " + path.join("."),
        icon: SlidersHorizontal,
        run: () => void focusField(path),
      });
    }
    list.push(
      {
        id: "action:save",
        group: "操作",
        title: "立即保存配置",
        keywords: "save ctrl+s",
        icon: Save,
        run: () =>
          void save().then((ok) => {
            if (ok) toast("配置已保存", "success");
          }),
      },
      { id: "action:add-group", group: "操作", title: "添加群", icon: Hash, run: go("/conversations?add=1") },
      { id: "action:add-provider", group: "操作", title: "添加服务商", icon: Sparkles, run: go("/providers?add=1") },
      { id: "action:light", group: "操作", title: "切换到浅色主题", keywords: "theme", icon: Sun, run: () => setTheme("light") },
      { id: "action:dark", group: "操作", title: "切换到深色主题", keywords: "theme", icon: Moon, run: () => setTheme("dark") },
      { id: "action:system", group: "操作", title: "主题跟随系统", keywords: "theme", icon: Monitor, run: () => setTheme("system") },
      { id: "action:restart", group: "操作", title: "重启 Harness", keywords: "restart", icon: RotateCw, run: () => void restartHarness() },
    );
    return list;
  }, [state.draft, groups, tools, files, sessions]);
}

const defaultGroups = new Set(["页面", "操作"]);

/** Ctrl/⌘+K 打开；方向键选择，回车执行，Esc 关闭。 */
export function CommandPalette() {
  const { palette: open } = useUi();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);
  return (
    <dialog
      ref={dialog}
      className="dialog palette"
      aria-label="命令面板"
      onCancel={(event) => {
        event.preventDefault();
        setPalette(false);
      }}
      onClose={() => setPalette(false)}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setPalette(false);
      }}
    >
      {/* 只在打开时挂载，避免启动时就读取工具目录和文件列表。 */}
      {open && <PaletteBody />}
    </dialog>
  );
}

function PaletteBody() {
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const commands = useCommands();
  useEffect(() => {
    void filesResource.refresh();
  }, []);
  const results = useMemo(() => {
    const text = query.trim().toLowerCase();
    if (!text) return commands.filter((item) => defaultGroups.has(item.group));
    const terms = text.split(/\s+/);
    const counts = new Map<string, number>();
    return commands.filter((item) => {
      const haystack = (item.title + " " + (item.detail ?? "") + " " + (item.keywords ?? "")).toLowerCase();
      if (!terms.every((term) => haystack.includes(term))) return false;
      const count = (counts.get(item.group) ?? 0) + 1;
      counts.set(item.group, count);
      return count <= 8;
    });
  }, [commands, query]);
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>("[aria-selected=true]")
      ?.scrollIntoView({ block: "nearest" });
  }, [active, results]);
  const run = (command: Command | undefined) => {
    if (!command) return;
    setPalette(false);
    command.run();
  };
  let lastGroup = "";
  return (
    <>
      <div className="palette-search">
        <Search size={17} aria-hidden="true" />
        <input
          autoFocus
          value={query}
          placeholder="搜索页面、会话、工具、设置项…"
          aria-label="搜索命令"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={results[active] ? "palette-" + active : undefined}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setActive((current) => (results.length ? (current + step + results.length) % results.length : 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              run(results[active]);
            }
          }}
        />
      </div>
      <div className="palette-list" id="palette-list" role="listbox" ref={listRef} aria-label="搜索结果">
        {results.length === 0 && <p className="palette-empty">没有匹配的结果</p>}
        {results.map((command, index) => {
          const Icon = command.icon;
          const heading = command.group !== lastGroup ? command.group : "";
          lastGroup = command.group;
          return (
            <div key={command.id} role="presentation">
              {heading && (
                <div className="palette-group" role="presentation">
                  {heading}
                </div>
              )}
              <div
                id={"palette-" + index}
                role="option"
                aria-selected={index === active}
                className={"palette-item" + (index === active ? " active" : "")}
                onPointerMove={() => setActive(index)}
                onClick={() => run(command)}
              >
                <Icon size={16} aria-hidden="true" />
                <span className="palette-title">{command.title}</span>
                {command.detail && <span className="palette-detail">{command.detail}</span>}
                {index === active && <CornerDownLeft size={14} className="palette-enter" aria-hidden="true" />}
              </div>
            </div>
          );
        })}
      </div>
      <footer className="palette-footer">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd> 选择
        </span>
        <span>
          <kbd>Enter</kbd> 打开
        </span>
        <span>
          <kbd>Esc</kbd> 关闭
        </span>
      </footer>
    </>
  );
}
