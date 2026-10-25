/** 外壳：侧栏导航、顶栏（面包屑 + 保存状态 + 操作）、右栏（本页目录 + 写回记录）。 */

import { Cat, Menu, Moon, Power, RotateCw, Search, Sun } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent } from "react";

import {
  config,
  dirtyLeaves,
  issueCount,
  useConfig,
  useDirtyUnder,
  usePageDirtyCount,
  type ConfigState,
  type Receipt,
  type SyncStatus,
} from "@/lib/config";
import { files, useFiles, type FileStatus } from "@/lib/files";
import { cls, NAV_GROUPS, navItem, type NavItem } from "@/lib/pages";
import { getIn, startsWith } from "@/lib/path";
import { requestPower } from "@/lib/power";
import { hrefFor, navigate, scrollToSection, useRoute } from "@/lib/router";
import { useStore } from "@/lib/store";
import { toggleTheme, useTheme } from "@/lib/theme";
import { tocStore, type TocItem } from "@/lib/toc";
import type { Path, PathSeg } from "@/lib/types";
import { openPalette, setMenu } from "@/lib/ui";
import { pageForPath, SECTION_LABELS, type PageKey } from "@/schema";

/* ---------- 侧栏 ---------- */

function pageIssueCount(state: ConfigState, page: PageKey): number {
  let count = 0;
  for (const key of state.clientIssues.keys()) {
    if (pageForPath(JSON.parse(key) as PathSeg[]) === page) count += 1;
  }
  for (const issue of state.serverIssues) {
    if (issue.path.length > 0 && pageForPath(issue.path) === page && !state.clientIssues.has(JSON.stringify(issue.path))) count += 1;
  }
  return count;
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const configDirty = usePageDirtyCount(item.key);
  const fileDirty = useFiles((state) => (state.open !== null && state.open.content !== state.open.savedContent ? 1 : 0));
  const dirty = item.key === "files" ? fileDirty : configDirty;
  const errors = useConfig((state) => (item.key === "files" ? 0 : pageIssueCount(state, item.key)));
  const enabled = useConfig((state) => (item.plugin ? getIn(state.draft, ["plugins", item.plugin]) != null : null));
  const Icon = item.icon;
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    setMenu(false);
    navigate({ page: item.key });
  };
  return (
    <a
      href={hrefFor({ page: item.key })}
      className={cls("nav-item", active && "is-active")}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
    >
      <span className="nav-ic">
        <Icon className="ic" aria-hidden />
      </span>
      <span className="nav-label">{item.label}</span>
      {enabled !== null ? (
        <span className={cls("nav-state", enabled && "is-on")} title={enabled ? "已启用" : "未启用"} aria-label={enabled ? "已启用" : "未启用"} />
      ) : null}
      {errors > 0 ? (
        <span className="nav-count nav-count--bad" title={`${errors} 处需要修正`}>
          {errors}
        </span>
      ) : dirty > 0 ? (
        <span className="nav-count" title={item.key === "files" ? "当前文件有未保存修改" : `${dirty} 处待写入`}>
          {dirty}
        </span>
      ) : null}
    </a>
  );
}

function SideFoot() {
  const meta = useConfig((state) => state.meta);
  if (!meta) return <div className="side-foot">未连接</div>;
  return (
    <div className="side-foot" title={meta.watcher_active ? "保存后插件配置自动热载" : "当前进程没有监听配置文件，插件改动要重启后生效"}>
      <span className={cls("foot-dot", !meta.watcher_active && "foot-dot--idle")} aria-hidden />
      插件配置 r{meta.plugin_revision} · {meta.watcher_active ? "监听文件变更" : "未启用热载"}
    </div>
  );
}

export function Sidebar() {
  const route = useRoute();
  return (
    <aside className="side" aria-label="导航">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          <Cat className="ic" />
        </span>
        <span className="brand-text">
          <strong>MyBot</strong>
          <small>mybot.toml 控制台</small>
        </span>
      </div>
      <button type="button" className="side-search" onClick={openPalette}>
        <Search className="ic" aria-hidden />
        <span>搜索字段、页面与操作</span>
        <kbd>Ctrl K</kbd>
      </button>
      <nav className="nav" aria-label="配置页面">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <p className="nav-title">{group.title}</p>
            <ul>
              {group.items.map((item) => (
                <li key={item.key}>
                  <NavLink item={item} active={route.page === item.key} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <SideFoot />
    </aside>
  );
}

/* ---------- 保存状态 ---------- */

type Tone = "muted" | "pending" | "busy" | "ok" | "bad" | "warn";

interface StatusView {
  tone: Tone;
  label: string;
  detail: string;
}

function describeConfig(status: SyncStatus, dirty: number, issues: number, restart: string[]): StatusView {
  const restartNote = restart.length > 0 ? `${restart.map((key) => SECTION_LABELS[key] ?? key).join("、")}的改动要重启进程后生效。` : "";
  switch (status.kind) {
    case "idle":
      return dirty > 0
        ? { tone: "pending", label: `待写入 ${dirty} 处`, detail: "停止编辑 0.8 秒后自动写入；Ctrl S 立即写入。" }
        : { tone: "muted", label: "与 mybot.toml 一致", detail: `没有待写入的修改。${restartNote}` };
    case "pending":
      return { tone: "pending", label: `待写入 ${dirty} 处`, detail: "停止编辑 0.8 秒后自动写入；Ctrl S 立即写入。" };
    case "saving":
      return { tone: "busy", label: "写入中…", detail: "正在校验并写入 mybot.toml。" };
    case "saved":
      return { tone: "ok", label: `已写入 ${status.at}`, detail: `已写入 mybot.toml，注释与格式保持不变。${restartNote}` };
    case "watching":
      return { tone: "busy", label: "热生效中…", detail: "已写入，等待插件重新加载配置。" };
    case "applied":
      return { tone: "ok", label: `已热生效 r${status.revision}`, detail: `插件已按新配置运行。${restartNote}` };
    case "invalid":
      return { tone: "bad", label: `${Math.max(issues, 1)} 处需要修正`, detail: "修正后会自动写入；各页顶部列出了具体问题。" };
    case "conflict":
      return { tone: "bad", label: "mybot.toml 已被外部修改", detail: "保存时发现文件在别处被改过，需要选择如何处理。" };
    case "hold":
      return { tone: "warn", label: "外部修改未处理，下次保存将覆盖", detail: "你选择了继续编辑：下一次写入会用当前表单覆盖外部修改。" };
    case "error":
      return { tone: "bad", label: `未写入：${status.message}`, detail: "修改仍保留在表单里；排除问题后按 Ctrl S 重试。" };
  }
}

function describeFile(status: FileStatus, hasFile: boolean): StatusView {
  switch (status.kind) {
    case "idle":
      return { tone: "muted", label: hasFile ? "与磁盘一致" : "未打开文件", detail: "文本文件停止输入 1 秒后自动保存。" };
    case "pending":
      return { tone: "pending", label: "待保存", detail: "停止输入 1 秒后自动保存；Ctrl S 立即保存。" };
    case "saving":
      return { tone: "busy", label: "保存中…", detail: "正在写入文件。" };
    case "saved":
      return { tone: "ok", label: `已保存 ${status.at}`, detail: "已写入 config/ 下的文件。" };
    case "invalid":
      return { tone: "bad", label: status.message, detail: "被插件引用的提示词不能为空，补上内容后自动保存。" };
    case "conflict":
      return { tone: "bad", label: "文件已被外部修改", detail: "保存时发现文件在别处被改过，需要选择如何处理。" };
    case "hold":
      return { tone: "warn", label: "外部修改未处理，下次保存将覆盖", detail: "下一次保存会用编辑器里的内容覆盖外部修改。" };
    case "error":
      return { tone: "bad", label: `未保存：${status.message}`, detail: "内容仍保留在编辑器里；排除问题后按 Ctrl S 重试。" };
  }
}

function receiptState(receipt: Receipt): string {
  switch (receipt.state) {
    case "applied":
      return `已热生效 r${receipt.revision ?? ""}`;
    case "watching":
      return "热生效中…";
    case "restart":
      return "已写入 · 重启后生效";
    default:
      return "已写入";
  }
}

export function ReceiptLog({ limit = 6, onJump }: { limit?: number; onJump?: () => void }) {
  const receipts = useConfig((state) => state.receipts);
  if (receipts.length === 0) return <p className="log-empty">这次打开后还没有写入。</p>;
  return (
    <ol className="log">
      {receipts.slice(0, limit).map((receipt) => (
        <li key={receipt.id} className={`log-entry log-entry--${receipt.state}`}>
          <div className="log-meta">
            <time>{receipt.time}</time>
            <span className="log-state">{receiptState(receipt)}</span>
          </div>
          <ul className="log-changes">
            {receipt.changes.slice(0, 4).map((change) => (
              <li key={change.key}>
                <button
                  type="button"
                  className="log-label"
                  title="跳到这个字段"
                  onClick={() => {
                    onJump?.();
                    navigate({ page: change.page }, { reveal: change.path });
                  }}
                >
                  {change.label}
                </button>
                <span className="log-diff">
                  <s>{change.from}</s>
                  <span className="log-arrow">→</span>
                  <b>{change.to}</b>
                </span>
              </li>
            ))}
            {receipt.changes.length > 4 ? <li className="log-more">另有 {receipt.changes.length - 4} 处</li> : null}
          </ul>
        </li>
      ))}
    </ol>
  );
}

function StatusPill() {
  const route = useRoute();
  const onFiles = route.page === "files";
  const status = useConfig((state) => state.status);
  const dirty = useConfig((state) => dirtyLeaves(state).length);
  const issues = useConfig(issueCount);
  const restart = useConfig((state) => state.restartSections);
  const fileStatus = useFiles((state) => state.status);
  const hasFile = useFiles((state) => state.open !== null);
  const fileDirty = useFiles((state) => state.open !== null && state.open.content !== state.open.savedContent);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const view = onFiles ? describeFile(fileStatus, hasFile) : describeConfig(status, dirty, issues, restart);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="top-status" ref={root}>
      <button
        type="button"
        className={`st st--${view.tone}`}
        aria-expanded={open}
        aria-haspopup="true"
        title="查看保存状态与写回记录"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="st-dot" aria-hidden />
        <span className="st-label" aria-live="polite">
          {view.label}
        </span>
      </button>
      {open ? (
        <div className="st-pop" role="dialog" aria-label="保存状态与写回记录">
          <div className="st-pop-head">
            <span className="st-pop-title">{onFiles ? "文本文件" : "mybot.toml 写回"}</span>
            <kbd>Ctrl S</kbd>
          </div>
          <p className="st-pop-detail">{view.detail}</p>
          <div className="st-pop-actions">
            {onFiles ? (
              <button type="button" className="btn btn--small btn--primary" disabled={!fileDirty} onClick={() => void files.saveNow()}>
                立即保存
              </button>
            ) : (
              <>
                <button type="button" className="btn btn--small btn--primary" disabled={dirty === 0} onClick={() => void config.saveNow()}>
                  立即写入
                </button>
                <button type="button" className="btn btn--small" onClick={() => void config.validateNow()}>
                  校验配置
                </button>
              </>
            )}
          </div>
          <p className="rail-title">
            写回记录<span className="rail-sub">本次会话</span>
          </p>
          <ReceiptLog limit={10} onJump={() => setOpen(false)} />
        </div>
      ) : null}
    </div>
  );
}

function RestartChip() {
  const sections = useConfig((state) => state.restartSections);
  if (sections.length === 0) return null;
  const labels = sections.map((key) => SECTION_LABELS[key] ?? key).join("、");
  return (
    <button
      type="button"
      className="restart-chip"
      title={`${labels} 的改动已写入，重启进程后生效。点击重启`}
      onClick={() => requestPower("restart")}
    >
      <RotateCw className="ic" aria-hidden />
      <span>{labels} 待重启生效</span>
    </button>
  );
}

function ThemeButton() {
  const { dark } = useTheme();
  return (
    <button
      type="button"
      className="icon-btn"
      aria-label={dark ? "切换到亮色主题" : "切换到暗色主题"}
      title={dark ? "切换到亮色主题" : "切换到暗色主题"}
      onClick={toggleTheme}
    >
      {dark ? <Moon className="ic" aria-hidden /> : <Sun className="ic" aria-hidden />}
    </button>
  );
}

export function TopBar() {
  const route = useRoute();
  const item = navItem(route.page);
  return (
    <header className="top">
      <button type="button" className="icon-btn top-menu" aria-label="打开导航" onClick={() => setMenu(true)}>
        <Menu className="ic" aria-hidden />
      </button>
      <div className="crumbs">
        <span>{item.group}</span>
        <span className="crumb-sep">/</span>
        <strong>{item.label}</strong>
      </div>
      <StatusPill />
      <div className="top-actions">
        <RestartChip />
        <button type="button" className="icon-btn hide-narrow" aria-label="命令面板" title="命令面板 Ctrl K" onClick={openPalette}>
          <Search className="ic" aria-hidden />
        </button>
        <ThemeButton />
        <button type="button" className="icon-btn hide-narrow" aria-label="重启 MyBot" title="重启 MyBot" onClick={() => requestPower("restart")}>
          <RotateCw className="ic" aria-hidden />
        </button>
        <button type="button" className="icon-btn icon-btn--danger" aria-label="关闭 MyBot" title="关闭 MyBot" onClick={() => requestPower("shutdown")}>
          <Power className="ic" aria-hidden />
        </button>
      </div>
    </header>
  );
}

/* ---------- 右栏 ---------- */

function hasIssueUnder(state: ConfigState, prefixes: readonly Path[]): boolean {
  for (const key of state.clientIssues.keys()) {
    const path = JSON.parse(key) as PathSeg[];
    if (prefixes.some((prefix) => startsWith(path, prefix))) return true;
  }
  return state.serverIssues.some(
    (issue) => issue.path.length > 0 && prefixes.some((prefix) => startsWith(issue.path, prefix)),
  );
}

function TocEntry({ item, current }: { item: TocItem; current: boolean }) {
  const dirty = useDirtyUnder(item.prefixes);
  const error = useConfig((state) => hasIssueUnder(state, item.prefixes));
  return (
    <li>
      <button
        type="button"
        className={cls("toc-item", current && "is-current", dirty && "is-dirty", error && "is-error")}
        onClick={() => scrollToSection(item.id)}
      >
        <span>{item.title}</span>
        <i className="toc-dot" aria-hidden />
      </button>
    </li>
  );
}

function useScrollSpy(ids: string[]): string | null {
  const key = ids.join("|");
  const [current, setCurrent] = useState<string | null>(null);
  useEffect(() => {
    const scroller = document.getElementById("page");
    if (!scroller) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const limit = scroller.getBoundingClientRect().top + 110;
      let first: string | null = null;
      let last: string | null = null;
      let found: string | null = null;
      for (const id of ids) {
        const element = document.getElementById(`sec-${id}`);
        if (!element || element.closest("[data-filtered]")) continue;
        first ??= id;
        last = id;
        if (element.getBoundingClientRect().top <= limit) found = id;
      }
      // 滚到底时最后几节可能永远顶不到阈值，直接高亮最后一节；页面本身不能滚动时不算。
      const scrolled = scroller.scrollTop > 0;
      if (scrolled && scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) found = last;
      setCurrent(found ?? first);
    };
    const onScroll = () => {
      if (frame === 0) frame = requestAnimationFrame(update);
    };
    update();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
    // ids 由 key 代表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return current;
}

export function Rail() {
  const items = useStore(tocStore, (state) => state.items);
  const current = useScrollSpy(items.map((item) => item.id));
  return (
    <aside className="rail" aria-label="本页目录与写回记录">
      {items.length > 0 ? (
        <div className="rail-block">
          <p className="rail-title">本页目录</p>
          <ol className="toc">
            {items.map((item) => (
              <TocEntry key={item.id} item={item} current={current === item.id} />
            ))}
          </ol>
        </div>
      ) : null}
      <div className="rail-block">
        <p className="rail-title">
          写回记录<span className="rail-sub">本次会话</span>
        </p>
        <ReceiptLog limit={8} />
      </div>
    </aside>
  );
}
