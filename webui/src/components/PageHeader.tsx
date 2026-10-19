/** 页头：小标题 + 得意黑大标题 + 说明，下方是改动计数与本页筛选；以及本页问题横幅。 */

import { ChevronDown, LocateFixed, RotateCw, Search, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";

import { usePageDirtyCount, useConfig, type ConfigState } from "@/lib/config";
import { cls } from "@/lib/pages";
import { pathKey } from "@/lib/path";
import { navigate, revealField } from "@/lib/router";
import type { PathSeg } from "@/lib/types";
import { describePath, humanizeIssue, pageForPath, type PageKey } from "@/schema";

interface PageHeaderProps {
  page: PageKey;
  kicker: string;
  table: string;
  title: string;
  desc: ReactNode;
  notice?: string;
  aside?: ReactNode;
  filter?: string;
  onFilter?: (value: string) => void;
}

export function PageHeader({ page, kicker, table, title, desc, notice, aside, filter, onFilter }: PageHeaderProps) {
  const dirty = usePageDirtyCount(page);
  return (
    <header className="page-head">
      <div className="page-kicker">
        <span>{kicker}</span>
        <code>{table}</code>
      </div>
      <div className="page-title-row">
        <h1 className="page-title">{title}</h1>
        {aside}
      </div>
      <p className="page-desc">{desc}</p>
      {notice ? (
        <p className="page-notice">
          <RotateCw className="ic" aria-hidden />
          {notice}
        </p>
      ) : null}
      {onFilter ? (
        <div className="page-tools">
          <span className={cls("page-dirty", dirty > 0 && "has")} aria-live="polite">
            {dirty > 0 ? `本页 ${dirty} 处待写入` : "与 mybot.toml 一致"}
          </span>
          <label className="filter">
            <Search className="ic" aria-hidden />
            <input
              id="page-filter"
              type="search"
              value={filter}
              placeholder="筛选本页字段"
              aria-label="筛选本页字段"
              autoComplete="off"
              onChange={(event) => onFilter(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape" && filter) {
                  event.preventDefault();
                  onFilter("");
                }
              }}
            />
            <kbd>/</kbd>
          </label>
        </div>
      ) : null}
    </header>
  );
}

export function FilterEmpty({ query }: { query: string }) {
  return (
    <p className="filter-empty">
      本页没有包含「{query}」的字段。按 <kbd>Ctrl K</kbd> 可以在全部页面里搜索。
    </p>
  );
}

/* ---------- 问题横幅 ---------- */

interface PageIssue {
  key: string;
  path: PathSeg[];
  label: string;
  message: string;
}

function collect(state: ConfigState, page: PageKey): PageIssue[] {
  const out: PageIssue[] = [];
  const seen = new Set<string>();
  for (const [key, message] of state.clientIssues) {
    const path = JSON.parse(key) as PathSeg[];
    if (pageForPath(path) !== page) continue;
    seen.add(key);
    out.push({ key, path, label: describePath(path, state.draft), message });
  }
  for (const issue of state.serverIssues) {
    const key = issue.path.length > 0 ? pathKey(issue.path) : `@${issue.location}`;
    if (seen.has(key)) continue;
    if (issue.path.length > 0 && pageForPath(issue.path) !== page) continue;
    seen.add(key);
    out.push({
      key,
      path: issue.path,
      label: issue.path.length > 0 ? describePath(issue.path, state.draft) : issue.location,
      message: humanizeIssue(issue.error_type, issue.message),
    });
  }
  return out;
}

function sameIssues(a: PageIssue[], b: PageIssue[]): boolean {
  return a.length === b.length && a.every((item, index) => item.key === b[index]?.key && item.message === b[index]?.message && item.label === b[index]?.label);
}

const COLLAPSED = 4;

export function IssueBanner({ page }: { page: PageKey }) {
  const issues = useConfig((state) => collect(state, page), sameIssues);
  const [expanded, setExpanded] = useState(false);
  if (issues.length === 0) return null;
  const shown = expanded ? issues : issues.slice(0, COLLAPSED);
  return (
    <section className="issues" aria-label="需要修正的配置">
      <button
        type="button"
        className="issues-head"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
        disabled={issues.length <= COLLAPSED}
      >
        <TriangleAlert className="ic" aria-hidden />
        本页 {issues.length} 处需要修正，修正后会自动写入
        {issues.length > COLLAPSED ? (
          <span className="issues-toggle">
            {expanded ? "收起" : `展开全部`}
            <ChevronDown className="ic" aria-hidden />
          </span>
        ) : null}
      </button>
      <ul className="issues-list">
        {shown.map((issue) => (
          <li className="issue" key={issue.key}>
            <span className="issue-label">{issue.label}</span>
            <span className="issue-msg">{issue.message}</span>
            {issue.path.length > 0 ? (
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => {
                  navigate({ page: pageForPath(issue.path) });
                  revealField(issue.path);
                }}
              >
                <LocateFixed className="ic" aria-hidden />
                定位
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
