import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { request, ApiError, errorMessage } from "../lib/api";
import {
  loadConfig,
  pollConfig,
  recordReceipt,
  useConfig,
} from "../lib/config";
import { navigate, setNavigationGuard } from "../lib/navigation";
import type { FileEnvelope } from "../lib/types";
import { ask, notify, setFileStatus } from "../lib/ui";
import { Dialog } from "../components/Dialog";
const Editor = lazy(() => import("../components/CodeEditor"));
const Preview = lazy(() => import("../components/MarkdownPreview"));
export function FilesPage({ path }: { path: string }) {
  const config = useConfig(),
    [files, setFiles] = useState<string[]>([]),
    [loaded, setLoaded] = useState<FileEnvelope | null>(null),
    [content, setContent] = useState(""),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false),
    [conflict, setConflict] = useState<FileEnvelope | null>(null),
    [conflictOpen, setConflictOpen] = useState(false),
    [mode, setMode] = useState<"edit" | "split" | "preview">("edit");
  const latestContent = useRef(content),
    savedContent = useRef(loaded?.content),
    savingRef = useRef(false),
    generation = useRef(0);
  latestContent.current = content;
  savedContent.current = loaded?.content;
  const dirty = loaded !== null && content !== loaded.content,
    isMarkdown = path.toLowerCase().endsWith(".md");
  const reloadList = useCallback(
    () =>
      request<{ files: string[] }>("/api/files/list")
        .then((data) => setFiles(data.files))
        .catch((reason) => setError(errorMessage(reason))),
    [],
  );
  useEffect(() => {
    void reloadList();
  }, [reloadList]);
  useEffect(() => {
    const ticket = ++generation.current;
    setLoaded(null);
    setError("");
    setConflict(null);
    setConflictOpen(false);
    if (!path) return;
    request<FileEnvelope>("/api/files?path=" + encodeURIComponent(path))
      .then((data) => {
        if (generation.current === ticket) {
          setLoaded(data);
          setContent(data.content);
        }
      })
      .catch((reason) => {
        if (generation.current === ticket) setError(errorMessage(reason));
      });
  }, [path]);
  const saveFile = useCallback(
    async (overrideSha?: string): Promise<boolean> => {
      if (!loaded || savingRef.current || (conflict && !overrideSha))
        return false;
      if (!dirty && !overrideSha) return true;
      if (path === config.draft.persona_file && !latestContent.current.trim()) {
        setError("人格提示词不能为空");
        return false;
      }
      const submitted = latestContent.current,
        ticket = generation.current;
      savingRef.current = true;
      setSaving(true);
      setError("");
      try {
        const result = await request<FileEnvelope>("/api/files", {
          method: "PUT",
          body: JSON.stringify({
            path,
            content: submitted,
            sha: overrideSha || loaded.sha,
          }),
        });
        if (ticket !== generation.current) return true;
        setLoaded(result);
        recordReceipt("文件已写入", [path]);
        setContent((current) =>
          current === submitted ? result.content : current,
        );
        setConflict(null);
        setConflictOpen(false);
        if (path.toLowerCase().endsWith(".toml"))
          void (config.loaded && !config.repairPath
            ? pollConfig()
            : loadConfig());
        return true;
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 409) {
          try {
            const latest = await request<FileEnvelope>(
              "/api/files?path=" + encodeURIComponent(path),
            );
            if (ticket === generation.current) {
              setConflict(latest);
              setConflictOpen(true);
              setError("文件已被外部修改，本地内容已保留");
            }
          } catch (refreshError) {
            setError(errorMessage(refreshError));
          }
        } else if (ticket === generation.current)
          setError(errorMessage(reason));
        return false;
      } finally {
        savingRef.current = false;
        setSaving(false);
      }
    },
    [
      loaded,
      conflict,
      dirty,
      path,
      config.draft.persona_file,
      config.loaded,
      config.repairPath,
    ],
  );
  useEffect(() => {
    if (!dirty || conflict || saving || error) return;
    const timer = setTimeout(() => void saveFile(), 1000);
    return () => clearTimeout(timer);
  }, [dirty, content, conflict, saving, error, saveFile]);
  useEffect(() => {
    setFileStatus({
      dirty,
      saving,
      error,
      conflict: !!conflict,
      save: () => void saveFile(),
      resolve: () => setConflictOpen(true),
    });
    setNavigationGuard(async () => {
      while (savingRef.current)
        await new Promise((resolve) => setTimeout(resolve, 50));
      if (!loaded || latestContent.current === savedContent.current)
        return true;
      return (
        (await saveFile()) ||
        (await ask("离开当前文件", "本地内容尚未写入，离开会丢失这些编辑。", {
          danger: true,
          confirm: "丢弃并离开",
        })) === true
      );
    });
    return () => {
      setFileStatus(null);
      setNavigationGuard(null);
    };
  }, [dirty, saving, error, conflict, saveFile, loaded]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveFile();
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [saveFile]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  const openFile = (next: string) =>
    navigate("/files?path=" + encodeURIComponent(next));
  return (
    <>
      <div className="file-toolbar">
        <label>
          文件{" "}
          <select
            value={path}
            onChange={(event) => void openFile(event.target.value)}
          >
            <option value="">选择配置文件</option>
            {[...new Set([...files, ...(path ? [path] : [])])].map((file) => (
              <option key={file} value={file}>
                {file}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={async () => {
            const name = await ask(
              "新建文本文件",
              "输入配置目录内的相对路径；父目录必须已经存在。",
              { input: "", placeholder: "例如 notes.md", confirm: "新建" },
            );
            if (typeof name !== "string" || !name.trim()) return;
            try {
              await request<FileEnvelope>("/api/files", {
                method: "PUT",
                body: JSON.stringify({
                  path: name.trim(),
                  content: "",
                  sha: null,
                }),
              });
              await reloadList();
              await openFile(name.trim());
            } catch (reason) {
              notify(errorMessage(reason));
            }
          }}
        >
          ＋ 新建文件
        </button>
        <span className={"badge " + (dirty ? "pending" : "ok")}>
          {saving ? "写入中" : dirty ? "待写入" : "已写入"}
        </span>
        {path && (
          <button
            onClick={() => void saveFile()}
            disabled={saving || !dirty || !!conflict}
          >
            立即保存
          </button>
        )}
      </div>
      {error && (
        <p className="notice bad">
          {error}
          {conflict && (
            <button className="small" onClick={() => setConflictOpen(true)}>
              处理冲突
            </button>
          )}
        </p>
      )}
      {loaded ? (
        <section className="sec editor-sec">
          <header className="sec-head">
            <code>{path}</code>
            {isMarkdown && (
              <div className="segmented">
                {(["edit", "split", "preview"] as const).map((item) => (
                  <button
                    key={item}
                    className={mode === item ? "active" : ""}
                    onClick={() => setMode(item)}
                  >
                    {{ edit: "编辑", split: "并排", preview: "预览" }[item]}
                  </button>
                ))}
              </div>
            )}
            <span className="muted">
              {content.length.toLocaleString()} 字符
            </span>
          </header>
          <div className={"editor-layout " + (isMarkdown ? mode : "edit")}>
            <Suspense fallback={<div className="empty">正在载入编辑器…</div>}>
              {(!isMarkdown || mode !== "preview") && (
                <Editor
                  value={content}
                  onChange={(value) => {
                    setContent(value);
                    setError(
                      conflict ? "文件已被外部修改，本地内容已保留" : "",
                    );
                  }}
                  markdownMode={isMarkdown}
                  onSave={() => void saveFile()}
                />
              )}
              {isMarkdown && mode !== "edit" && <Preview content={content} />}
            </Suspense>
          </div>
        </section>
      ) : !path ? (
        <div className="empty">选择一个人格、知识库或配置文件开始编辑。</div>
      ) : !error ? (
        <div className="empty">正在读取文件…</div>
      ) : null}
      <Dialog
        open={conflictOpen && !!conflict}
        title="文本文件发生外部修改"
        onClose={() => setConflictOpen(false)}
      >
        <div className="dialog-body">
          <p>已获取最新版本标识，你的本地内容没有改变。</p>
          <p className="muted">
            覆盖只针对当前文件；如果外部文件再次变化，会重新提示冲突。
          </p>
        </div>
        <div className="dialog-actions wrap">
          <button
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(content);
                notify("本地内容已复制");
              } catch (reason) {
                notify("复制失败：" + errorMessage(reason));
              }
            }}
          >
            复制本地内容
          </button>
          <button onClick={() => setConflictOpen(false)}>继续编辑</button>
          <button
            onClick={() => {
              if (!conflict) return;
              setLoaded(conflict);
              setContent(conflict.content);
              setConflict(null);
              setConflictOpen(false);
              setError("");
            }}
          >
            丢弃并刷新
          </button>
          <button
            className="primary"
            onClick={async () => {
              try {
                const latest = await request<FileEnvelope>(
                  "/api/files?path=" + encodeURIComponent(path),
                );
                await saveFile(latest.sha);
              } catch (reason) {
                setError(errorMessage(reason));
              }
            }}
          >
            覆盖外部修改
          </button>
        </div>
      </Dialog>
    </>
  );
}
