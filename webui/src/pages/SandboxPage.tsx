import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { errorMessage, post, request } from "../lib/api";
import { formatBytes } from "../lib/types";
import type { SandboxFile, SandboxStatus } from "../lib/types";
import { ask, notify } from "../lib/ui";
import { Section } from "../components/Forms";
import { sandboxSection } from "../schema/fields";
export function SandboxPage({ filter }: { filter: string }) {
  const [status, setStatus] = useState<SandboxStatus | null>(null),
    [files, setFiles] = useState<SandboxFile[]>([]),
    [path, setPath] = useState(""),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [error, setError] = useState(""),
    [pending, setPending] = useState(false);
  const load = useCallback(async () => {
    try {
      const next = await request<SandboxStatus>("/api/sandbox/status");
      setStatus(next);
      setError("");
      if (next.available) {
        const listing = await request<{ path: string; files: SandboxFile[] }>(
          "/api/sandbox/files?path=" + encodeURIComponent(path),
        );
        setFiles(listing.files);
      } else setFiles([]);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, [path]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);
  const cleanup = async (mode: "cache" | "selected" | "all") => {
    const label = {
      cache: "清理依赖缓存",
      selected: "删除选中文件",
      all: "清空共享工作区",
    }[mode];
    const detail =
      mode === "all"
        ? "所有获准使用沙箱的会话共用此空间。此操作会永久删除代码、依赖、下载文件和产物；配置、人格与知识库不会被删除。"
        : mode === "cache"
          ? "将删除共享工作区中的依赖缓存，之后安装依赖可能需要重新下载。代码与产物保留。"
          : "将永久删除选中的 " +
            selected.size +
            " 个文件或目录，其他会话也无法再读取这些文件。";
    if ((await ask(label, detail, { danger: true, confirm: label })) !== true)
      return;
    setPending(true);
    try {
      await post("/api/sandbox/cleanup", {
        mode,
        paths: [...selected],
        confirmed: true,
      });
      setSelected(new Set());
      await load();
      notify(label + "完成");
    } catch (reason) {
      notify(errorMessage(reason));
    } finally {
      setPending(false);
    }
  };
  const percentage =
    status && status.quota_bytes
      ? Math.min(100, (status.used_bytes / status.quota_bytes) * 100)
      : 0;
  return (
    <>
      <p className="notice">
        一个共享、持久的工作区。获准使用沙箱的群与私聊共用文件；聊天与执行状态不写入这里。
      </p>
      {error && <p className="notice bad">{error}</p>}
      {status?.configuration_notice && (
        <p className="notice">{status.configuration_notice}</p>
      )}
      <section className="sec">
        <header className="sec-head">
          <div>
            <h2>工作区状态</h2>
            <p>磁盘使用底层硬配额，代码只能访问公网 HTTP / HTTPS。</p>
          </div>
          <span className={"badge " + (status?.available ? "ok" : "bad")}>
            {status?.available ? (status.busy ? "正在执行" : "可用") : "不可用"}
          </span>
        </header>
        <div className="sandbox-overview">
          <div className="quota-value">
            <strong>{formatBytes(status?.used_bytes ?? 0)}</strong>
            <span>/ {formatBytes(status?.quota_bytes ?? 50_000_000_000)}</span>
          </div>
          <div
            className="meter"
            style={{ "--progress": percentage + "%" } as CSSProperties}
          >
            <span />
          </div>
          <p className="muted">
            {status?.reason || "Python 3.14 · uv · Node.js 24 · npm · Git"}
          </p>
          <div className="button-row">
            <button onClick={() => void load()}>刷新</button>
            {status?.busy && (
              <button
                className="danger"
                onClick={async () => {
                  if (
                    (await ask(
                      "停止代码执行",
                      "将停止当前共享沙箱中的执行进程，已经写入的文件保留。",
                      { danger: true, confirm: "停止执行" },
                    )) !== true
                  )
                    return;
                  try {
                    await post("/api/sandbox/cancel");
                    await load();
                  } catch (reason) {
                    notify(errorMessage(reason));
                  }
                }}
              >
                停止执行
              </button>
            )}
            <button
              disabled={!status?.available || status.busy || pending}
              onClick={() => void cleanup("cache")}
            >
              清理缓存
            </button>
            <button
              className="danger"
              disabled={!status?.available || status.busy || pending}
              onClick={() => void cleanup("all")}
            >
              清空工作区
            </button>
          </div>
          {status?.busy && (
            <small className="field-hint">
              清理前请先停止执行，避免删除正在使用的文件。
            </small>
          )}
        </div>
      </section>
      <section className="sec">
        <header className="sec-head">
          <div>
            <h2>共享文件</h2>
            <p>删除操作只作用于选定文件，不影响应用配置。</p>
          </div>
          <button
            className="danger small"
            disabled={!selected.size || !!status?.busy || pending}
            onClick={() => void cleanup("selected")}
          >
            删除选中 ({selected.size})
          </button>
        </header>
        <div className="directory-bar">
          <button
            className="small"
            disabled={!path}
            onClick={() => {
              setPath(path.split("/").slice(0, -1).join("/"));
              setSelected(new Set());
            }}
          >
            ↑ 上一级
          </button>
          <code>/workspace{path ? "/" + path : ""}</code>
        </div>
        <div className="file-table">
          {files
            .filter(
              (file) =>
                !filter ||
                file.name.toLowerCase().includes(filter.toLowerCase()),
            )
            .map((file) => (
              <div className="file-row" key={file.path}>
                <input
                  type="checkbox"
                  aria-label={"选择 " + file.name}
                  checked={selected.has(file.path)}
                  onChange={(event) => {
                    const next = new Set(selected);
                    if (event.target.checked) next.add(file.path);
                    else next.delete(file.path);
                    setSelected(next);
                  }}
                />
                <span className="file-symbol">{file.is_dir ? "▤" : "▧"}</span>
                {file.is_dir ? (
                  <button
                    className="text-button"
                    onClick={() => {
                      setPath(file.path);
                      setSelected(new Set());
                    }}
                  >
                    {file.name}
                  </button>
                ) : (
                  <span className="filename">{file.name}</span>
                )}
                <span className="file-size">
                  {file.is_dir ? "目录" : formatBytes(file.size)}
                </span>
              </div>
            ))}
          {!files.length && (
            <div className="empty">
              {status?.available ? "此目录为空。" : "沙箱可用后显示文件。"}
            </div>
          )}
        </div>
      </section>
      <Section definition={sandboxSection} base={["sandbox"]} filter={filter} />
    </>
  );
}
