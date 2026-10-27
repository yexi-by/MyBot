import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, CircleAlert, FileText, LoaderCircle, PencilLine, TriangleAlert } from "lucide-react";
import { ApiError, errorMessage, get, isOffline, put } from "../lib/api";
import { pollConfig, recordSave } from "../lib/config";
import { formatClock, formatNumber } from "../lib/format";
import { setNavigationGuard } from "../lib/navigation";
import type { FileEnvelope } from "../lib/types";
import { confirm, setActiveSaver, toast } from "../lib/ui";
import { Dialog } from "./Dialog";
import { Banner, Button, EmptyState, Segmented, Spinner } from "./primitives";
import { copyText } from "./shell";

const CodeEditor = lazy(() => import("./CodeEditor"));
const MarkdownPreview = lazy(() => import("./MarkdownPreview"));

type Mode = "edit" | "split" | "preview";
const modes: { value: Mode; label: string }[] = [
  { value: "edit", label: "编辑" },
  { value: "split", label: "分栏" },
  { value: "preview", label: "预览" },
];
function readMode(): Mode {
  try {
    const value = localStorage.getItem("editor-mode");
    return value === "split" || value === "preview" ? value : "edit";
  } catch {
    return "edit";
  }
}

const fileUrl = (path: string) => "/api/files?path=" + encodeURIComponent(path);

/**
 * 配置目录中的文本文件编辑器：停止输入 1 秒后自动保存，
 * 每 5 秒检查外部修改；冲突时暂停保存并提供覆盖、放弃、继续编辑与复制本地内容。
 */
export function FileEditor({
  path,
  kind,
  toolbar,
  note,
  showPath = true,
}: {
  path: string;
  kind: "markdown" | "text" | "toml";
  toolbar?: ReactNode;
  note?: ReactNode;
  /** 页面标题已显示路径时隐藏工具栏里的路径。 */
  showPath?: boolean;
}) {
  const [remote, setRemote] = useState<FileEnvelope | null>(null);
  const [content, setContent] = useState("");
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState<FileEnvelope | null>(null);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [savedAt, setSavedAt] = useState(0);
  const [mode, setMode] = useState<Mode>(readMode);
  const failed = useRef<{ content: string; retryable: boolean } | null>(null);
  const retryDelay = useRef(1000);
  const latest = useRef({ remote, content, conflict, saving });
  latest.current = { remote, content, conflict, saving };
  const dirty = remote !== null && content !== remote.content;

  useEffect(() => {
    let cancelled = false;
    setRemote(null);
    setContent("");
    setLoadError("");
    setSaveError("");
    setConflict(null);
    setConflictOpen(false);
    failed.current = null;
    retryDelay.current = 1000;
    get<FileEnvelope>(fileUrl(path)).then(
      (data) => {
        if (cancelled) return;
        setRemote(data);
        setContent(data.content);
      },
      (error: unknown) => {
        if (!cancelled)
          setLoadError(
            error instanceof ApiError && error.status === 404 ? "文件不存在，可能已被移动或删除" : errorMessage(error),
          );
      },
    );
    return () => {
      cancelled = true;
    };
  }, [path]);

  const showConflict = useCallback(async () => {
    try {
      const fresh = await get<FileEnvelope>(fileUrl(path));
      if (fresh.content === latest.current.content) {
        setRemote(fresh);
        setConflict(null);
        failed.current = null;
        setSaveError("");
        return true;
      }
      setConflict(fresh);
      setConflictOpen(true);
    } catch (error) {
      setSaveError("读取最新内容失败：" + errorMessage(error));
    }
    return false;
  }, [path]);

  const write = useCallback(
    async (text: string, sha: string): Promise<boolean> => {
      setSaving(true);
      setSaveError("");
      try {
        const data = await put<FileEnvelope>("/api/files", { path, content: text, sha });
        setRemote(data);
        setConflict(null);
        failed.current = null;
        retryDelay.current = 1000;
        setSavedAt(Date.now());
        recordSave(path, []);
        if (kind === "toml") void pollConfig();
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) return await showConflict();
        else {
          const retryable = isOffline(error) || (error instanceof ApiError && (error.status >= 500 || error.status === 408 || error.status === 429));
          failed.current = { content: text, retryable };
          retryDelay.current = Math.min(30_000, retryDelay.current * 2);
          setSaveError(errorMessage(error));
        }
        return false;
      } finally {
        setSaving(false);
      }
    },
    [path, kind, showConflict],
  );

  const saveNow = useCallback(async (): Promise<boolean> => {
    const current = latest.current;
    if (!current.remote || current.conflict || current.saving) return false;
    if (current.content === current.remote.content) return true;
    return write(current.content, current.remote.sha);
  }, [write]);

  useEffect(() => {
    if (!dirty || conflict || saving || (content === failed.current?.content && !failed.current.retryable)) return;
    const timer = setTimeout(() => void saveNow(), content === failed.current?.content ? retryDelay.current : 1000);
    return () => clearTimeout(timer);
  }, [content, dirty, conflict, saving, saveNow]);

  const loaded = remote !== null;
  useEffect(() => {
    if (!loaded) return;
    const timer = setInterval(async () => {
      const current = latest.current;
      if (document.hidden || !current.remote || current.saving) return;
      try {
        const fresh = await get<FileEnvelope>(fileUrl(path));
        if (fresh.sha === (current.conflict ?? current.remote).sha) return;
        if (current.conflict) setConflict(fresh);
        else if (current.content === current.remote.content) {
          setRemote(fresh);
          setContent(fresh.content);
        } else {
          setConflict(fresh);
          setConflictOpen(true);
        }
      } catch {
        /* 断连由全局横幅提示；文件被删除时下次保存会报错 */
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [path, loaded]);

  useEffect(() => {
    setActiveSaver(() => {
      const current = latest.current;
      if (current.conflict) {
        setConflictOpen(true);
        return;
      }
      void saveNow().then((ok) => {
        if (ok) toast("已保存 " + path, "success");
      });
    });
    setNavigationGuard(async () => {
      const current = latest.current;
      if (!current.remote || current.content === current.remote.content) return true;
      if (!current.conflict && (await saveNow())) return true;
      return confirm({
        title: "放弃未保存的修改？",
        message: current.conflict
          ? "文件已被外部修改，你的内容还没有保存。离开前可以先复制本地内容。"
          : "文件没有保存成功，离开后这些修改会丢失。",
        confirmLabel: "放弃并离开",
        danger: true,
      });
    });
    const unload = (event: BeforeUnloadEvent) => {
      const current = latest.current;
      if (current.remote && current.content !== current.remote.content) event.preventDefault();
    };
    window.addEventListener("beforeunload", unload);
    return () => {
      setActiveSaver(null);
      setNavigationGuard(null);
      window.removeEventListener("beforeunload", unload);
    };
  }, [path, saveNow]);

  if (loadError)
    return (
      <div className="file-editor">
        <EmptyState icon={CircleAlert} title="无法打开文件">
          {loadError}
        </EmptyState>
      </div>
    );

  const status = !remote
    ? { icon: LoaderCircle, text: "正在读取…", tone: "neutral", spin: true }
    : saving
      ? { icon: LoaderCircle, text: "保存中…", tone: "neutral", spin: true }
      : conflict
        ? { icon: TriangleAlert, text: "外部已修改", tone: "warning" }
        : saveError
          ? { icon: CircleAlert, text: "保存失败", tone: "danger" }
          : dirty
            ? { icon: PencilLine, text: "未保存", tone: "neutral" }
            : { icon: Check, text: savedAt ? "已保存 " + formatClock(savedAt / 1000) : "已保存", tone: "success" };
  const StatusIcon = status.icon;
  const markdownMode = kind === "markdown";
  const view: Mode = markdownMode ? mode : "edit";
  const lines = content ? content.split("\n").length : 0;

  return (
    <div className="file-editor">
      <div className="editor-toolbar">
        <div className="editor-file">
          {showPath && (
            <>
              <FileText size={16} aria-hidden="true" />
              <span className="mono truncate" title={path}>
                {path}
              </span>
            </>
          )}
          {toolbar}
        </div>
        <span className={"save-status save-" + status.tone} role="status">
          <StatusIcon size={15} className={status.spin ? "spin" : undefined} aria-hidden="true" />
          <span>{status.text}</span>
        </span>
        {markdownMode && (
          <Segmented
            size="sm"
            label="编辑器布局"
            value={mode}
            options={modes}
            onChange={(next) => {
              setMode(next);
              try {
                localStorage.setItem("editor-mode", next);
              } catch {
                /* 忽略 */
              }
            }}
          />
        )}
      </div>
      {conflict && !conflictOpen && (
        <Banner
          tone="warning"
          icon={TriangleAlert}
          title="文件已被外部修改"
          actions={
            <Button size="sm" onClick={() => setConflictOpen(true)}>
              处理冲突
            </Button>
          }
        >
          自动保存已暂停。
        </Banner>
      )}
      {saveError && (
        <Banner tone="danger" icon={CircleAlert} title="保存失败">
          {saveError}
        </Banner>
      )}
      <div className={"editor-body mode-" + view}>
        {!remote ? (
          <div className="editor-loading">
            <Spinner label="正在读取文件" />
          </div>
        ) : (
          <>
            {view !== "preview" && (
              <Suspense fallback={<Spinner label="正在加载编辑器" />}>
                <CodeEditor
                  value={content}
                  onChange={setContent}
                  markdownMode={markdownMode}
                  label={path + " 的内容"}
                />
              </Suspense>
            )}
            {view !== "edit" && (
              <Suspense fallback={<Spinner label="正在加载预览" />}>
                <MarkdownPreview content={content} />
              </Suspense>
            )}
          </>
        )}
      </div>
      <div className="editor-footer">
        <span className="num">
          {formatNumber(content.length)} 字符 · {formatNumber(lines)} 行
        </span>
        {note && <span className="grow editor-note">{note}</span>}
      </div>
      <Dialog
        open={conflictOpen && !!conflict}
        title="文件已被外部修改"
        description={path + " 在你编辑期间被其他程序或手动修改，自动保存已暂停。"}
        onClose={() => setConflictOpen(false)}
        footer={
          <>
            <Button onClick={() => setConflictOpen(false)} data-autofocus>
              继续编辑
            </Button>
            <Button
              onClick={async () => {
                if (await copyText(content)) toast("已复制本地内容", "success");
                else toast("复制失败，请在编辑器中全选后手动复制", "danger");
              }}
            >
              复制本地内容
            </Button>
            <Button
              variant="danger-ghost"
              onClick={() => {
                if (!conflict) return;
                setRemote(conflict);
                setContent(conflict.content);
                setConflict(null);
                setConflictOpen(false);
                toast("已加载最新内容");
              }}
            >
              放弃我的修改
            </Button>
            <Button
              variant="primary"
              loading={saving}
              onClick={async () => {
                if (!conflict) return;
                if (await write(latest.current.content, conflict.sha)) {
                  setConflictOpen(false);
                  toast("已用你的内容覆盖", "success");
                }
              }}
            >
              用我的内容覆盖
            </Button>
          </>
        }
      >
        <p className="secondary">
          覆盖会用你当前的内容替换整个文件，外部修改将丢失；放弃会加载最新内容并丢弃你的修改。选择继续编辑可以先对照修改，之后再处理。
        </p>
      </Dialog>
    </div>
  );
}
