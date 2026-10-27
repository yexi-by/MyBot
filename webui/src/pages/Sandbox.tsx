import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box,
  ChevronRight,
  CircleAlert,
  CircleStop,
  Eraser,
  File,
  Folder,
  FolderOpen,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { ConfigSection, FieldShell } from "../components/form";
import { Badge, Banner, Button, EmptyState, Meter, Spinner, Switch, Tabs } from "../components/primitives";
import { PageHeader } from "../components/shell";
import { errorMessage, get, post } from "../lib/api";
import { change, useConfig } from "../lib/config";
import { formatBytes } from "../lib/format";
import { navigate } from "../lib/navigation";
import { sandboxResource, useResource } from "../lib/resource";
import { object } from "../lib/types";
import type { SandboxFile } from "../lib/types";
import { confirm, toast } from "../lib/ui";
import { fieldId, sandboxSection } from "../schema/fields";

type Mode = "cache" | "selected" | "all";

async function cleanup(mode: Mode, paths: string[] = []): Promise<boolean> {
  const names = paths.map((path) => path.split("/").pop() || path);
  const text = {
    cache: {
      title: "清理依赖缓存？",
      message: "删除共享工作区里的依赖缓存，之后安装依赖可能需要重新下载。代码和产物保留。",
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
    await post("/api/sandbox/cleanup", { mode, paths, confirmed: true });
    toast(mode === "cache" ? "已清理依赖缓存" : mode === "all" ? "已清空共享工作区" : "已删除", "success");
    void sandboxResource.refresh();
    return true;
  } catch (error) {
    toast(errorMessage(error), "danger");
    return false;
  }
}

function Files({ busy }: { busy: boolean }) {
  const [path, setPath] = useState("");
  const [files, setFiles] = useState<SandboxFile[] | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [loading, setLoading] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [total, setTotal] = useState(0);
  const sequence = useRef(0);
  const load = useCallback(async (offset = 0) => {
    const request = ++sequence.current;
    setLoading(true);
    try {
      const data = await get<{ path: string; files: SandboxFile[]; total: number; next_offset: number | null }>(
        "/api/sandbox/files?path=" + encodeURIComponent(path) + "&offset=" + offset,
      );
      if (request !== sequence.current) return;
      setFiles((current) => offset ? [...(current ?? []), ...data.files] : data.files);
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
    return () => { sequence.current++; };
  }, [load]);
  const parts = path ? path.split("/") : [];
  const all = files ?? [];
  const allSelected = all.length > 0 && all.every((file) => selected.has(file.path));
  const toggle = (file: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  return (
    <section className="card">
      <header className="card-header wrap">
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
        {selected.size > 0 && (
          <Button
            size="sm"
            variant="danger"
            icon={Trash2}
            disabled={busy}
            title={busy ? "沙箱正在执行，先停止后才能删除" : undefined}
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
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void load()}>
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
        <EmptyState icon={FolderOpen} title={path ? "这个目录是空的" : "工作区还是空的"} compact>
          {path ? "" : "Agent 执行代码时写入的文件会出现在这里。"}
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
                <th className="num-cell">大小</th>
              </tr>
            </thead>
            <tbody>
              {all.map((file) => (
                <tr key={file.path} className={selected.has(file.path) ? "selected" : undefined}>
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
                  <td className="num-cell num secondary">{file.is_dir ? "—" : formatBytes(file.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!error && all.length > 0 && <div className="card-body row wrap">
        <span className="muted small">已显示 {all.length} / {total} 项</span>
        {nextOffset !== null && <Button size="sm" loading={loading} onClick={() => void load(nextOffset)}>加载下一页</Button>}
      </div>}
    </section>
  );
}

function SandboxOverview() {
  const status = useResource(sandboxResource, 15_000);
  const data = status.data;
  if (!data)
    return status.error ? (
      <Banner tone="danger" icon={CircleAlert} title="无法读取沙箱状态">
        {status.error}
      </Banner>
    ) : (
      <Spinner label="正在检查沙箱状态" />
    );
  const used = data.used_bytes;
  const ratio = used !== null && data.quota_bytes ? used / data.quota_bytes : 0;
  const state = !data.available
    ? { text: "不可用", tone: "danger" as const }
    : data.busy
      ? { text: "执行中", tone: "accent" as const }
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
          <h2 className="grow">共享工作区</h2>
          <Badge tone={state.tone}>{state.text}</Badge>
        </header>
        <div className="card-body stack">
          <div className="usage">
            <div className="usage-value">
              <strong className="num">{formatBytes(used)}</strong>
              <span className="muted"> / {formatBytes(data.quota_bytes)}</span>
            </div>
            <Meter
              label="空间用量"
              value={used ?? 0}
              max={data.quota_bytes}
              tone={ratio >= 0.9 ? "danger" : ratio >= 0.75 ? "warning" : "accent"}
            />
            <p className="muted small">
              {data.available
                ? "Python 3.14、uv、Node.js 24、npm、Git 可用。磁盘用量受底层硬配额限制。"
                : data.reason || "沙箱控制器没有响应，请检查 Runner 容器。"}
            </p>
          </div>
          <div className="row wrap">
            {data.busy && (
              <Button
                variant="danger"
                icon={CircleStop}
                onClick={async () => {
                  const accepted = await confirm({
                    title: "停止正在执行的代码？",
                    message: "执行进程会被终止，已经写入的文件保留。",
                    confirmLabel: "停止执行",
                    danger: true,
                  });
                  if (!accepted) return;
                  try {
                    await post("/api/sandbox/cancel");
                    toast("已停止执行", "success");
                    void sandboxResource.refresh();
                  } catch (error) {
                    toast(errorMessage(error), "danger");
                  }
                }}
              >
                停止执行
              </Button>
            )}
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
            <span className="grow" />
            <Button
              variant="ghost"
              icon={RefreshCw}
              loading={status.loading}
              onClick={() => void sandboxResource.refresh()}
            >
              刷新
            </Button>
          </div>
          {data.busy && <p className="muted small">执行期间不能清理或删除文件。</p>}
          {!!data.queued && <p className="muted small">另有 {data.queued} 项操作正在等待工作区。</p>}
        </div>
      </section>
      <Files busy={data.busy} />
    </div>
  );
}

function SandboxSettings() {
  const state = useConfig();
  const enabled = object(state.draft.sandbox).enabled === true;
  const id = fieldId(["sandbox", "enabled"]);
  return (
    <div className="stack">
      <section className="card form-section">
        <div className="form-grid">
          <FieldShell
            id={id}
            label="启用共享沙箱"
            restart
            help="启用后，开放了沙箱能力的会话可以执行代码并读写共享工作区。需要先部署 Runner 容器。"
          >
            <Switch
              id={id}
              checked={enabled}
              label="启用共享沙箱"
              onChange={async (next) => {
                if (
                  !next &&
                  !(await confirm({
                    title: "停用共享沙箱？",
                    message: "停用后 Agent 不能再执行代码或读写共享工作区，已有文件保留。",
                    confirmLabel: "停用",
                    danger: true,
                  }))
                )
                  return;
                change(["sandbox", "enabled"], next);
              }}
            />
          </FieldShell>
        </div>
      </section>
      <ConfigSection def={sandboxSection} base={["sandbox"]} />
    </div>
  );
}

export function SandboxPage({ view }: { view: "overview" | "settings" }) {
  const state = useConfig();
  const enabled = object(state.draft.sandbox).enabled === true;
  return (
    <div className="page">
      <PageHeader
        title="沙箱"
        description="获准的会话共用一个持久工作区来执行代码；代码只能访问公网 HTTP 和 HTTPS"
        save={view === "settings"}
      />
      <Tabs
        label="沙箱页面"
        value={view}
        onChange={(next) => void navigate(next === "settings" ? "/sandbox/settings" : "/sandbox")}
        items={[
          { id: "overview", label: "工作区" },
          { id: "settings", label: "设置" },
        ]}
      />
      {view === "settings" ? (
        <SandboxSettings />
      ) : enabled ? (
        <SandboxOverview />
      ) : (
        <EmptyState
          icon={Box}
          title="共享沙箱未启用"
          action={<Button onClick={() => void navigate("/sandbox/settings")}>打开设置</Button>}
        >
          启用后，开放了沙箱能力的会话可以执行 Python 和 Node.js 代码，并在共享工作区中读写文件。
        </EmptyState>
      )}
    </div>
  );
}
