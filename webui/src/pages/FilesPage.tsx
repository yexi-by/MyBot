/** 配置文本页：config/ 文件树 + 编辑器；.md 可并排预览，其余文件纯文本编辑。 */

import { ChevronDown, Copy, File, FileText, Folder, FolderOpen, Plus, RefreshCw, TriangleAlert } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState, type CSSProperties } from "react";

import { Dialog, useLatest } from "@/components/Dialog";
import { PageHeader } from "@/components/PageHeader";
import { config, useConfig } from "@/lib/config";
import { CONFIG_FILE, files, useFiles, type FileView } from "@/lib/files";
import { cls } from "@/lib/pages";
import { replaceRoute, useRoute } from "@/lib/router";
import { fileReferences, requiredPromptFiles, type FileReference } from "@/schema/plugins";

const MarkdownPreview = lazy(() => import("@/components/MarkdownPreview"));
// 编辑器只在这一页用到，单独分包，首屏不加载 CodeMirror。
const CodeEditor = lazy(() => import("@/components/CodeEditor").then((module) => ({ default: module.CodeEditor })));

const isMarkdown = (path: string) => path.toLowerCase().endsWith(".md");

/* ---------- 文件树 ---------- */

interface TreeDir {
  name: string;
  path: string;
  dirs: TreeDir[];
  files: string[];
}

function buildTree(paths: string[]): TreeDir {
  const root: TreeDir = { name: "", path: "", dirs: [], files: [] };
  for (const path of paths) {
    const parts = path.split("/");
    let node = root;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const dirPath = parts.slice(0, index + 1).join("/");
      let child = node.dirs.find((dir) => dir.path === dirPath);
      if (!child) {
        child = { name: parts[index] ?? "", path: dirPath, dirs: [], files: [] };
        node.dirs.push(child);
      }
      node = child;
    }
    node.files.push(path);
  }
  const sort = (dir: TreeDir) => {
    dir.dirs.sort((a, b) => a.name.localeCompare(b.name));
    dir.files.sort((a, b) => a.localeCompare(b));
    dir.dirs.forEach(sort);
  };
  sort(root);
  return root;
}

function refBadge(path: string, refs: Map<string, FileReference[]>): { text: string; soft: boolean } | null {
  if (path === CONFIG_FILE) return { text: "主配置", soft: true };
  const list = refs.get(path);
  if (!list) return null;
  const personas = list.filter((ref) => ref.label.endsWith("人格")).length;
  if (personas > 0) return { text: `${personas} 个群`, soft: false };
  if (list.some((ref) => ref.required)) return { text: "必需", soft: false };
  return { text: "知识库", soft: true };
}

interface TreeProps {
  dir: TreeDir;
  depth: number;
  openPath: string | null;
  dirtyPath: string | null;
  refs: Map<string, FileReference[]>;
  collapsed: Set<string>;
  onToggle: (path: string) => void;
}

function TreeItems({ dir, depth, openPath, dirtyPath, refs, collapsed, onToggle }: TreeProps) {
  return (
    <>
      {dir.files.map((path) => {
        const badge = refBadge(path, refs);
        const active = path === openPath;
        return (
          <li key={path}>
            <button
              type="button"
              className={cls("ft-file", active && "is-active")}
              style={{ "--d": depth } as CSSProperties}
              aria-current={active ? "true" : undefined}
              title={path}
              onClick={() => void files.requestOpen(path)}
            >
              {isMarkdown(path) ? <FileText className="ic" aria-hidden /> : <File className="ic" aria-hidden />}
              <span className="ft-name">{path.split("/").pop()}</span>
              {path === dirtyPath ? <span className="ft-dirty" title="有未保存的修改" /> : null}
              {badge ? <span className={cls("ft-ref", badge.soft && "ft-ref--soft")}>{badge.text}</span> : null}
            </button>
          </li>
        );
      })}
      {dir.dirs.map((child) => {
        const isCollapsed = collapsed.has(child.path);
        return (
          <li key={child.path}>
            <button
              type="button"
              className="ft-dir"
              style={{ "--d": depth } as CSSProperties}
              aria-expanded={!isCollapsed}
              onClick={() => onToggle(child.path)}
            >
              <ChevronDown className="ic ft-chev" aria-hidden />
              {isCollapsed ? <Folder className="ic" aria-hidden /> : <FolderOpen className="ic" aria-hidden />}
              <span>{child.name}/</span>
            </button>
            {isCollapsed ? null : (
              <ul>
                <TreeItems {...{ dir: child, depth: depth + 1, openPath, dirtyPath, refs, collapsed, onToggle }} />
              </ul>
            )}
          </li>
        );
      })}
    </>
  );
}

/* ---------- 对话框 ---------- */

function NewFileDialog({ open, dir, onClose }: { open: boolean; dir: string; onClose: () => void }) {
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  // 每次打开都从当前文件所在目录起步，不沿用上次没提交的输入。
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setPath(dir);
      setError("");
    }
  }
  const submit = async () => {
    setBusy(true);
    const message = await files.create(path);
    setBusy(false);
    if (message) {
      setError(message);
      return;
    }
    setPath("");
    setError("");
    onClose();
  };
  return (
    <Dialog open={open} onClose={onClose} labelledBy="new-file-title">
      <h2 className="dlg-title" id="new-file-title">
        新建文本文件
      </h2>
      <form
        className="dlg-body"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="dlg-field">
          <label htmlFor="new-file-path">相对 config/ 的路径</label>
          <input
            id="new-file-path"
            className={cls("inp is-mono", error && "is-error")}
            value={path}
            placeholder="ai_group_chat/prompts/roles/new-role.md"
            autoFocus
            spellCheck={false}
            autoComplete="off"
            onFocus={(event) => {
              const end = event.currentTarget.value.length;
              event.currentTarget.setSelectionRange(end, end);
            }}
            onChange={(event) => {
              setPath(event.target.value);
              if (error) setError("");
            }}
          />
          {error ? <p className="dlg-error">{error}</p> : <p className="dlg-note">所在目录必须已经存在；新文件是空的，写好内容后再在配置里引用它。</p>}
        </div>
        <div className="dlg-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="submit" className="btn btn--primary" disabled={busy || path.trim() === ""}>
            新建并打开
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function FileConflictDialog() {
  const openState = useFiles((state) => state.conflictOpen);
  const path = useFiles((state) => state.open?.path ?? "");
  const shownPath = useLatest(openState ? path : null) ?? path;
  const [busy, setBusy] = useState(false);
  const run = async (choice: "overwrite" | "discard" | "keep") => {
    setBusy(true);
    try {
      await files.resolveConflict(choice);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={openState} onClose={() => !busy && void run("keep")} className="dlg--danger dlg--wide" labelledBy="file-conflict-title">
      <h2 className="dlg-title" id="file-conflict-title">
        {shownPath} 已被外部修改
      </h2>
      <div className="dlg-body">
        <p>保存时发现这个文件在别处被改过。编辑器里的内容还没有保存，可以先复制一份留底。</p>
        <p className="dlg-note">
          覆盖：用编辑器里的内容写回，外部修改会丢失。丢弃并刷新：载入外部改过的版本。继续编辑：暂不处理，下一次保存时覆盖。
        </p>
      </div>
      <div className="dlg-actions">
        <button type="button" className="btn" onClick={() => void run("keep")} disabled={busy} autoFocus>
          继续编辑
        </button>
        <button type="button" className="btn btn--ghost" onClick={() => void files.copyLocal()} disabled={busy}>
          <Copy className="ic" aria-hidden />
          复制本地内容
        </button>
        <span className="spacer" />
        <button type="button" className="btn" onClick={() => void run("discard")} disabled={busy}>
          丢弃并刷新
        </button>
        <button type="button" className="btn btn--danger" onClick={() => void run("overwrite")} disabled={busy}>
          覆盖外部修改
        </button>
      </div>
    </Dialog>
  );
}

function SwitchDialog() {
  const pending = useFiles((state) => state.pendingSwitch);
  const shown = useLatest(pending);
  return (
    <Dialog open={pending !== null} onClose={() => files.cancelSwitch()} className="dlg--danger" labelledBy="switch-title">
      <h2 className="dlg-title" id="switch-title">
        切换文件会丢失没保存的修改
      </h2>
      <div className="dlg-body">
        <p>当前文件的修改没能保存（内容不能为空、冲突未处理或保存失败）。切换到 {shown} 后，这些修改会丢失。</p>
      </div>
      <div className="dlg-actions">
        <button type="button" className="btn" onClick={() => files.cancelSwitch()} autoFocus>
          留在当前文件
        </button>
        <button type="button" className="btn btn--danger" onClick={() => files.confirmSwitch()}>
          丢弃并切换
        </button>
      </div>
    </Dialog>
  );
}

/* ---------- 页面 ---------- */

const VIEWS: [FileView, string][] = [
  ["edit", "编辑"],
  ["split", "并排"],
  ["preview", "预览"],
];

export default function FilesPage({ active }: { active: boolean }) {
  const route = useRoute();
  const list = useFiles((state) => state.list);
  const listError = useFiles((state) => state.listError);
  const open = useFiles((state) => state.open);
  const openPath = open?.path ?? "";
  const loading = useFiles((state) => state.loading);
  const view = useFiles((state) => state.view);
  const revision = useFiles((state) => state.revision);
  const status = useFiles((state) => state.status);
  const draft = useConfig((state) => state.draft);
  const phase = useConfig((state) => state.phase);
  const loadError = useConfig((state) => state.loadError);
  const refs = useMemo(() => fileReferences(draft), [draft]);
  const required = useMemo(() => requiredPromptFiles(draft), [draft]);
  const tree = useMemo(() => buildTree(list ?? []), [list]);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    void files.refreshList();
  }, []);

  // 地址栏里的文件 → 打开；语法错误时默认打开 mybot.toml 修复。
  useEffect(() => {
    if (!active) return;
    const target = route.file ?? (phase === "broken" && !files.state.open ? CONFIG_FILE : undefined);
    if (target && target !== files.state.open?.path && target !== files.state.loading) void files.requestOpen(target);
  }, [active, route.file, phase]);

  // 打开的文件 → 地址栏（不新增历史记录）。
  useEffect(() => {
    if (!active || !open) return;
    if (route.page === "files" && route.file !== open.path) replaceRoute({ page: "files", file: open.path });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, open?.path]);

  const toggleDir = (path: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const dirty = open !== null && open.content !== open.savedContent;
  const markdown = open !== null && isMarkdown(open.path);
  const openRefs = open ? (refs.get(open.path) ?? []) : [];
  const emptyRequired = open !== null && open.content.trim() === "" && required.has(open.path);

  return (
    <div className="page-inner page-inner--files" hidden={!active}>
      <PageHeader
        page="files"
        kicker="文件"
        table="config/"
        title="配置文本"
        desc="提示词、知识库与 mybot.toml 原文。停止输入 1 秒后自动保存；被插件引用的提示词不能保存为空。"
      />
      {phase === "broken" ? (
        <div className="repair" role="alert">
          <TriangleAlert className="ic" aria-hidden />
          <div className="repair-msg">
            <strong>mybot.toml 语法有误，配置页暂时不可用。修好原文并保存后会自动恢复。</strong>
            <code>{loadError}</code>
          </div>
          <button type="button" className="btn" onClick={() => void config.load()}>
            <RefreshCw className="ic" aria-hidden />
            重新载入
          </button>
        </div>
      ) : null}
      <div className="files">
        <aside className="ftree" aria-label="config/ 文件">
          <div className="ftree-head">
            <span>config/</span>
            <span className="ftree-actions">
              <button type="button" className="icon-btn icon-btn--small" aria-label="刷新文件列表" title="刷新文件列表" onClick={() => void files.refreshList()}>
                <RefreshCw className="ic" aria-hidden />
              </button>
              <button type="button" className="btn btn--small" onClick={() => setCreating(true)}>
                <Plus className="ic" aria-hidden />
                新建
              </button>
            </span>
          </div>
          <ul className="ftree-list">
            {list === null ? (
              <li className="ftree-msg">{listError || "正在读取文件列表…"}</li>
            ) : list.length === 0 ? (
              <li className="ftree-msg">config/ 里还没有文本文件。</li>
            ) : (
              <TreeItems
                dir={tree}
                depth={0}
                openPath={open?.path ?? null}
                dirtyPath={dirty ? (open?.path ?? null) : null}
                refs={refs}
                collapsed={collapsed}
                onToggle={toggleDir}
              />
            )}
          </ul>
        </aside>
        <section className="fedit" aria-label="编辑器">
          {open ? (
            <>
              <div className="fedit-bar">
                <code className="fedit-path" title={`config/${open.path}`}>
                  {open.path}
                </code>
                {openRefs.length > 0 ? (
                  <span className={cls("fedit-tag", !openRefs.some((ref) => ref.required) && "fedit-tag--soft")} title={openRefs.map((ref) => ref.label).join("、")}>
                    {openRefs.length > 2 ? `被 ${openRefs.length} 处引用` : openRefs.map((ref) => ref.label).join("、")}
                  </span>
                ) : null}
                {emptyRequired ? <span className="fedit-tag fedit-tag--bad">必需文件不能为空</span> : null}
                {status.kind === "error" ? <span className="fedit-tag fedit-tag--bad">保存失败</span> : null}
                {markdown ? (
                  <div className="ctl-seg fedit-views" role="radiogroup" aria-label="视图">
                    {VIEWS.map(([value, label]) => (
                      <button
                        type="button"
                        role="radio"
                        key={value}
                        className="seg-opt"
                        aria-checked={view === value}
                        onClick={() => files.setView(value)}
                      >
                        <span>{label}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className={cls("fedit-split", !markdown && "is-plain")} data-view={markdown ? view : "edit"}>
                <div className="fedit-src">
                  <Suspense fallback={<p className="md-loading fedit-loading">正在加载编辑器…</p>}>
                    <CodeEditor
                      docKey={`${open.path}#${revision}`}
                      value={open.content}
                      isMarkdown={markdown}
                      onChange={(content) => files.edit(content)}
                      label={`编辑 ${open.path}`}
                    />
                  </Suspense>
                </div>
                {markdown && view !== "edit" ? (
                  <div className="fedit-preview">
                    <Suspense fallback={<p className="md-loading">正在加载预览…</p>}>
                      <MarkdownPreview content={open.content} />
                    </Suspense>
                  </div>
                ) : null}
              </div>
            </>
          ) : (
            <div className="fedit-empty">
              <div>
                <strong>{loading ? `正在打开 ${loading}…` : "从左边选一个文件"}</strong>
                {loading ? null : "提示词、知识库和 mybot.toml 原文都在这里编辑；也可以新建一个文件。"}
              </div>
            </div>
          )}
        </section>
      </div>
      <NewFileDialog open={creating} dir={openPath.includes("/") ? openPath.slice(0, openPath.lastIndexOf("/") + 1) : ""} onClose={() => setCreating(false)} />
      <FileConflictDialog />
      <SwitchDialog />
    </div>
  );
}
