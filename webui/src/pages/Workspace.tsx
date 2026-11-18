import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronRight,
  CircleAlert,
  CircleStop,
  Eraser,
  Eye,
  EyeOff,
  File,
  Folder,
  FolderOpen,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
  Trash2,
} from "lucide-react";
import { ConfigSection, FieldShell } from "../components/form";
import { Badge, Banner, Button, EmptyState, Meter, Segmented, Spinner, StatusDot, Switch, Tabs } from "../components/primitives";
import { DetailHeader, MasterDetail, QQAvatar } from "../components/shell";
import { errorMessage, get, post } from "../lib/api";
import { change, useConfig } from "../lib/config";
import { formatBytes, formatRelative } from "../lib/format";
import { linkHandler, navigate } from "../lib/navigation";
import { useResource, workspaceResource } from "../lib/resource";
import { object } from "../lib/types";
import type { WorkspaceFile } from "../lib/types";
import { confirm, toast } from "../lib/ui";
import { fieldId, workspaceSection } from "../schema/fields";

type Mode = "temp" | "cache" | "selected" | "all";
type SortBy = "name" | "mtime" | "size";

async function cleanup(mode: Mode, paths: string[] = []): Promise<boolean> {
  const names = paths.map((path) => path.split("/").pop() || path);
  const text = {
    temp: {
      title: "清理临时文件与执行日志？",
      message: "删除 .tmp、.scratch（临时媒体与中间帧）及 .outputs（旧命令输出日志）。用户目录文件与依赖缓存均保留。",
      label: "清理临时文件",
    },
    cache: {
      title: "清理依赖缓存？",
      message: "删除共享工作区里的依赖缓存（.cache），之后安装依赖可能需要重新下载。代码和产物保留。",
      label: "清理缓存",
    },
    selected: {
      title: "删除 " + paths.length + " 项？",
      message:
        "将永久删除：" +
        names.slice(0, 8).join("、") +
        (names.length > 8 ? " 等 " + names.length + " 项" : "") +
        "。所有会话都将无法再读取这些文件。",
      label: "删除",
    },
    all: {
      title: "清空共享工作区？",
      message: "所有会话共用这个工作区。代码、依赖、下载的文件和产物都会被永久删除；配置、人格和知识库不受影响。",
      label: "清空工作区",
    },
  }[mode];
  if (!(await confirm({ title: text.title, message: text.message, confirmLabel: text.label, danger: true })))
    return false;
  try {
    await post("/api/workspace/cleanup", { mode, paths, confirmed: true });
    toast(
      mode === "temp"
        ? "已清理临时文件与执行日志"
        : mode === "cache"
          ? "已清理依赖缓存"
          : mode === "all"
            ? "已清空共享工作区"
            : "已删除",
      "success",
    );
    void workspaceResource.refresh();
    return true;
  } catch (error) {
    toast(errorMessage(error), "danger");
    return false;
  }
}

function Files({ busy }: { busy: boolean }) {
  const [path, setPath] = useState("");
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [loading, setLoading] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [total, setTotal] = useState(0);
  const [hideSystem, setHideSystem] = useState(true);
  const [sortBy, setSortBy] = useState<SortBy>("mtime");
  const sequence = useRef(0);
  const load = useCallback(async (offset = 0) => {
    const request = ++sequence.current;
    setLoading(true);
    try {
      const data = await get<{ path: string; files: WorkspaceFile[]; total: number; next_offset: number | null }>(
        "/api/workspace/files?path=" + encodeURIComponent(path) + "&offset=" + offset,
      );
      if (request !== sequence.current) return;
      setFiles((current) => (offset ? [...(current ?? []), ...data.files] : data.files));
      if (!offset) {
        const visible = new Set(data.files.map((file) => file.path));
        setSelected((current) => (current.size ? new Set([...current].filter((item) => visible.has(item))) : current));
      }
      setNextOffset(data.next_offset);
      setTotal(data.total);
      setError("");
    } catch (reason) {
      if (request === sequence.current) setError(errorMessage(reason));
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    setSelected(new Set());
    void load();
    return () => {
      sequence.current++;
    };
  }, [load]);
  const parts = path ? path.split("/") : [];
  const rawAll = files ?? [];
  const isSysItem = (file: WorkspaceFile) =>
    Boolean(file.is_system) || (!path && file.name.startsWith("."));
  const hiddenCount = !path && hideSystem ? rawAll.filter(isSysItem).length : 0;
  const all = useMemo(() => {
    const filtered = !path && hideSystem ? rawAll.filter((file) => !isSysItem(file)) : [...rawAll];
    return filtered.sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      if (sortBy === "mtime") return (b.mtime ?? 0) - (a.mtime ?? 0) || a.name.localeCompare(b.name);
      if (sortBy === "size") return b.size - a.size || a.name.localeCompare(b.name);
      return a.name.localeCompare(b.name);
    });
  }, [rawAll, path, hideSystem, sortBy]);
  const allSelected = all.length > 0 && all.every((file) => selected.has(file.path));
  const toggle = (file: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  return (
    <section className="card files-card">
      <header className="card-header sticky wrap">
        <nav className="breadcrumb grow" aria-label="当前目录">
          <button type="button" onClick={() => setPath("")} aria-current={!path ? "location" : undefined}>
            工作区
          </button>
          {parts.map((part, index) => (
            <span key={index} className="row">
              <ChevronRight size={14} className="muted" aria-hidden="true" />
              <button
                type="button"
                onClick={() => setPath(parts.slice(0, index + 1).join("/"))}
                aria-current={index === parts.length - 1 ? "location" : undefined}
              >
                {part}
              </button>
            </span>
          ))}
        </nav>
        {!path && (
          <Button
            size="sm"
            variant="ghost"
            icon={hideSystem ? EyeOff : Eye}
            onClick={() => setHideSystem((v) => !v)}
            title={hideSystem ? "点击显示 .cache / .scratch / .outputs 等系统目录" : "折叠隐藏系统目录"}
          >
            {hideSystem
              ? "系统目录已折叠" + (hiddenCount > 0 ? `（${hiddenCount}）` : "")
              : "隐藏系统目录"}
          </Button>
        )}
        <Segmented
          size="sm"
          label="文件排序"
          value={sortBy}
          onChange={setSortBy}
          options={[
            { value: "mtime", label: "最近修改" },
            { value: "size", label: "大小" },
            { value: "name", label: "名称" },
          ]}
        />
        {selected.size > 0 && (
          <Button
            size="sm"
            variant="danger"
            icon={Trash2}
            disabled={busy}
            title={busy ? "工作区正在执行命令，先停止后才能批量删除" : undefined}
            onClick={async () => {
              if (await cleanup("selected", [...selected])) {
                setSelected(new Set());
                void load();
              }
            }}
          >
            删除所选（{selected.size}）
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          icon={RefreshCw}
          loading={loading}
          onClick={() => {
            void load();
            void workspaceResource.refresh();
          }}
        >
          刷新
        </Button>
      </header>
      {error ? (
        <div className="card-body">
          <p className="danger-text small">{error}</p>
        </div>
      ) : files === null ? (
        <div className="card-body">
          <Spinner label="正在读取文件" />
        </div>
      ) : all.length === 0 ? (
        <EmptyState
          icon={FolderOpen}
          title={path ? "这个目录是空的" : hiddenCount > 0 ? "暂无用户文件" : "工作区还是空的"}
          compact
        >
          {path
            ? ""
            : hiddenCount > 0
              ? `已自动折叠 ${hiddenCount} 个系统与缓存目录；Agent 创建的长期课题目录与文件会显示在这里。`
              : "Agent 写入的文件、脚本与媒体产物会出现在这里。"}
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table files">
            <thead>
              <tr>
                <th className="check-cell">
                  <input
                    type="checkbox"
                    aria-label="全选"
                    checked={allSelected}
                    onChange={() => setSelected(allSelected ? new Set() : new Set(all.map((file) => file.path)))}
                  />
                </th>
                <th>名称</th>
                <th className="num-cell">修改时间</th>
                <th className="num-cell">大小</th>
              </tr>
            </thead>
            <tbody>
              {all.map((file) => (
                <tr
                  key={file.path}
                  className={
                    [selected.has(file.path) ? "selected" : "", file.is_dir ? "is-dir" : ""].filter(Boolean).join(" ") ||
                    undefined
                  }
                  onClick={(event) => {
                    // 点目录所在行的空白处也能进入目录；勾选框和按钮保持各自行为。
                    if (!file.is_dir || (event.target as HTMLElement).closest("input, button")) return;
                    setPath(file.path);
                  }}
                >
                  <td className="check-cell">
                    <input
                      type="checkbox"
                      aria-label={"选择 " + file.name}
                      checked={selected.has(file.path)}
                      onChange={() => toggle(file.path)}
                    />
                  </td>
                  <td>
                    {file.is_dir ? (
                      <button type="button" className="file-name" onClick={() => setPath(file.path)}>
                        <Folder size={16} aria-hidden="true" />
                        {file.name}
                      </button>
                    ) : (
                      <span className="file-name">
                        <File size={16} aria-hidden="true" />
                        {file.name}
                      </span>
                    )}
                  </td>
                  <td className="num-cell num muted small">
                    {file.mtime ? formatRelative(file.mtime) : "—"}
                  </td>
                  <td className="num-cell num secondary">{file.is_dir ? "—" : formatBytes(file.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!error && all.length > 0 && (
        <footer className="card-footer">
          <span className="muted small num">
            已显示 {all.length} / {total} 项
          </span>
          <span className="grow" />
          {nextOffset !== null && (
            <Button size="sm" loading={loading} onClick={() => void load(nextOffset)}>
              加载下一页
            </Button>
          )}
        </footer>
      )}
    </section>
  );
}

function WorkspaceOverview() {
  const status = useResource(workspaceResource, 15_000);
  const data = status.data;
  if (!data)
    return status.error ? (
      <Banner tone="danger" icon={CircleAlert} title="无法读取工作区状态">
        {status.error}
      </Banner>
    ) : (
      <Spinner label="正在检查工作区状态" />
    );
  const used = data.used_bytes;
  const ratio = used !== null && data.max_bytes ? used / data.max_bytes : 0;
  const state = !data.available
    ? { text: "不可用", tone: "danger" as const }
    : data.over_limit
      ? { text: "空间已满", tone: "warning" as const }
      : data.busy
        ? {
            text:
              data.active_count && data.active_count > 1
                ? `执行中（${data.active_count}）`
                : "执行中",
            tone: "accent" as const,
          }
        : { text: "可用", tone: "success" as const };
  return (
    <div className="stack">
      {data.configuration_notice && (
        <Banner tone="warning" icon={CircleAlert}>
          {data.configuration_notice}
        </Banner>
      )}
      <section className="card">
        <header className="card-header wrap">
          <div className="grow">
            <h2>共享工作区</h2>
            <p className="card-subtitle mono">{data.root}</p>
          </div>
          <Badge tone={state.tone}>{state.text}</Badge>
        </header>
        <div className="card-body stack">
          <div className="usage">
            <div className="usage-value">
              <strong className="num">{formatBytes(used)}</strong>
              <span className="muted"> / {formatBytes(data.max_bytes)}</span>
            </div>
            <Meter
              label="空间用量"
              value={used ?? 0}
              max={data.max_bytes}
              tone={ratio >= 0.9 || data.over_limit ? "danger" : ratio >= 0.75 ? "warning" : "accent"}
            />
            <p className="muted small">
              {data.reason
                ? data.reason
                : "支持持久化文件读写、精确修改、全文搜索，以及 Python 3.14（uv）、Node.js 24、FFmpeg 与 Git 命令执行。"}
            </p>
          </div>
          <div className="row wrap">
            {data.busy && (
              <Button
                variant="danger"
                icon={CircleStop}
                onClick={async () => {
                  const accepted = await confirm({
                    title: "停止正在执行的命令？",
                    message: "当前运行的子进程会被终止，已经写入工作区的文件保留。",
                    confirmLabel: "停止执行",
                    danger: true,
                  });
                  if (!accepted) return;
                  try {
                    await post("/api/workspace/cancel");
                    toast("已停止执行", "success");
                    void workspaceResource.refresh();
                  } catch (error) {
                    toast(errorMessage(error), "danger");
                  }
                }}
              >
                停止执行
              </Button>
            )}
            <Button icon={Sparkles} disabled={data.busy} onClick={() => void cleanup("temp")}>
              清理临时与日志
            </Button>
            <Button icon={Eraser} disabled={data.busy} onClick={() => void cleanup("cache")}>
              清理依赖缓存
            </Button>
            <Button
              variant="danger-ghost"
              icon={Trash2}
              disabled={data.busy}
              onClick={() => void cleanup("all")}
            >
              清空工作区
            </Button>
          </div>
          {data.busy && <p className="muted small">命令执行期间不能批量清理或清空目录。</p>}
          {!!data.queued && <p className="muted small">另有 {data.queued} 项命令正在排队等待执行槽位。</p>}
        </div>
      </section>
      <Files busy={data.busy} />
    </div>
  );
}

function WorkspaceSettings() {
  const state = useConfig();
  const enabled = object(state.draft.workspace).enabled === true;
  const id = fieldId(["workspace", "enabled"]);
  return (
    <div className="stack">
      <section className="card form-section">
        <div className="form-grid">
          <FieldShell
            id={id}
            label="启用共享工作区"
            help="启用后，开放了「共享工作区」能力的会话可以持久化读写文件或执行本地工具链命令（保存后立即生效）。"
          >
            <Switch
              id={id}
              checked={enabled}
              label="启用共享工作区"
              onChange={async (next) => {
                if (
                  !next &&
                  !(await confirm({
                    title: "停用共享工作区？",
                    message: "停用后 Agent 不能再读写共享工作区或执行命令，已有文件保留。",
                    confirmLabel: "停用",
                    danger: true,
                  }))
                )
                  return;
                change(["workspace", "enabled"], next);
              }}
            />
          </FieldShell>
        </div>
      </section>
      <ConfigSection def={workspaceSection} base={["workspace"]} />
    </div>
  );
}

export function WorkspacePage({ view }: { view: "overview" | "settings" }) {
  const state = useConfig();
  const enabled = object(state.draft.workspace).enabled === true;
  const status = useResource(workspaceResource, enabled ? 15_000 : 0).data;
  const overviewSub = !enabled
    ? "未启用"
    : !status
      ? "正在读取工作区"
      : !status.available
        ? "不可用"
        : "已用 " + formatBytes(status.used_bytes) + " / " + formatBytes(status.max_bytes);
  const overviewTone = !enabled
    ? ("neutral" as const)
    : !status?.available
      ? ("danger" as const)
      : status.over_limit
        ? ("warning" as const)
        : status.busy
          ? ("accent" as const)
          : ("success" as const);

  return (
    <MasterDetail
      label="工作区"
      mode="section"
      hasDetail
      list={
        <>
          <div className="md-list-head">
            <span className="md-pane-title">共享工作区</span>
          </div>
          <ul className="md-items">
            <li>
              <a
                href="/workspace"
                className={"md-item" + (view === "overview" ? " active" : "")}
                aria-current={view === "overview" ? "page" : undefined}
                onClick={linkHandler("/workspace")}
              >
                <QQAvatar kind="icon" icon={FolderOpen} name="文件与空间" size="lg" />
                <span className="md-item-text">
                  <span className="qq-item-top">
                    <span className="md-item-title">文件与空间</span>
                    <StatusDot tone={overviewTone} />
                  </span>
                  <span className="qq-item-bottom">
                    <span className="md-item-sub">{overviewSub}</span>
                  </span>
                </span>
              </a>
            </li>
            <li>
              <a
                href="/workspace/settings"
                className={"md-item" + (view === "settings" ? " active" : "")}
                aria-current={view === "settings" ? "page" : undefined}
                onClick={linkHandler("/workspace/settings")}
              >
                <QQAvatar kind="icon" icon={SlidersHorizontal} name="设置" size="lg" />
                <span className="md-item-text">
                  <span className="qq-item-top">
                    <span className="md-item-title">设置</span>
                    <StatusDot tone={enabled ? "success" : "neutral"} />
                  </span>
                  <span className="qq-item-bottom">
                    <span className="md-item-sub">目录路径、空间预算与执行限制</span>
                  </span>
                </span>
              </a>
            </li>
          </ul>
        </>
      }
    >
      <div className="detail">
        <div className="detail-sticky">
          <DetailHeader
            avatar={
              <QQAvatar
                kind="icon"
                icon={view === "settings" ? SlidersHorizontal : FolderOpen}
                name={view === "settings" ? "工作区设置" : "工作区"}
                size="md"
              />
            }
            title={view === "settings" ? "工作区设置" : "工作区"}
            save={view === "settings"}
            subtitle="会话共用的持久化文件目录与本地工具链运行环境"
          />
          <div className="mobile-subnav">
            <Tabs
              label="工作区页面"
              value={view}
              onChange={(next) => void navigate(next === "settings" ? "/workspace/settings" : "/workspace")}
              items={[
                { id: "overview", label: "文件与空间" },
                { id: "settings", label: "设置" },
              ]}
            />
          </div>
        </div>
        <div className="detail-body stack">
          {view === "settings" ? (
            <WorkspaceSettings />
          ) : enabled ? (
            <WorkspaceOverview />
          ) : (
            <EmptyState
              icon={FolderOpen}
              title="共享工作区未启用"
              action={<Button onClick={() => void navigate("/workspace/settings")}>打开设置</Button>}
            >
              启用后，开放了「共享工作区」能力的会话可以持久化读写文件，并使用内置的 Python、Node.js、FFmpeg 与 Git 工具链。
            </EmptyState>
          )}
        </div>
      </div>
    </MasterDetail>
  );
}
