/** 配置文本编辑页：config/ 内 UTF-8 文件的自动保存编辑器，md 支持实时预览。 */

import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useFormContext } from "react-hook-form";
import {
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  File,
  FileCode2,
  FileText,
  Folder,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ApiError, listFiles, readFile, saveFile } from "@/lib/api";
import type { MyBotConfigData } from "@/lib/types";
import { cn } from "@/lib/utils";

interface OpenFile {
  path: string;
  content: string;
  sha256: string;
  dirty: boolean;
}

type FileSaveState =
  | "idle"
  | "editing"
  | "saving"
  | "saved"
  | "invalid"
  | "conflict"
  | "error";

const TEXT_AUTOSAVE_DELAY_MS = 1000;

const MarkdownEditor = lazy(async () => {
  const module = await import("@/components/MarkdownEditor");
  return { default: module.MarkdownEditor };
});

const MarkdownPreview = lazy(async () => {
  const module = await import("@/components/MarkdownPreview");
  return { default: module.MarkdownPreview };
});

function requiredPromptFiles(config: MyBotConfigData): Set<string> {
  const ai = config.plugins?.ai_group_chat;
  if (!ai) return new Set();
  const files = new Set<string>([ai.extra_requirements_file ?? ""]);
  if (ai.vision) {
    files.add(ai.vision.system_prompt_file);
    files.add(ai.vision.user_prompt_file);
  }
  for (const group of ai.groups ?? []) {
    files.add(group.system_prompt_file);
  }
  files.delete("");
  return files;
}

function isMarkdown(path: string): boolean {
  return path.toLowerCase().endsWith(".md");
}

function fileIcon(path: string) {
  const lower = path.toLowerCase();
  if (lower.endsWith(".md")) return FileText;
  if (/\.(toml|json|ya?ml)$/.test(lower)) return FileCode2;
  return File;
}

interface FileGroup {
  /** 顶层目录名；null 表示 config/ 根下的散文件。 */
  dir: string | null;
  files: string[];
}

/** 按顶层目录分组，根目录散文件在前，目录按名称排序。 */
function groupFiles(files: string[]): FileGroup[] {
  const root: string[] = [];
  const dirs = new Map<string, string[]>();
  for (const file of files) {
    const slash = file.indexOf("/");
    if (slash === -1) {
      root.push(file);
    } else {
      const dir = file.slice(0, slash);
      const list = dirs.get(dir) ?? [];
      list.push(file);
      dirs.set(dir, list);
    }
  }
  const groups: FileGroup[] = [];
  if (root.length > 0) groups.push({ dir: null, files: root });
  for (const dir of [...dirs.keys()].sort()) {
    groups.push({ dir, files: dirs.get(dir) ?? [] });
  }
  return groups;
}

/** 复制到剪贴板，非安全上下文（局域网 http）回退 execCommand。 */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const ok = document.execCommand("copy");
      textarea.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

export default function FilesPage({ initialPath }: { initialPath?: string }) {
  const [files, setFiles] = useState<string[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<OpenFile | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<FileSaveState>("idle");
  const [previewOn, setPreviewOn] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [pendingSwitch, setPendingSwitch] = useState<string | null>(null);
  const [closedGroups, setClosedGroups] = useState<Set<string>>(new Set());
  const blockedContent = useRef<string | null>(null);
  const latestOpenFile = useRef<OpenFile | null>(null);
  const editVersion = useRef(0);
  const { getValues } = useFormContext<MyBotConfigData>();

  const fileGroups = useMemo(() => groupFiles(files), [files]);

  useEffect(() => {
    latestOpenFile.current = openFile;
  }, [openFile]);

  const refreshFiles = useCallback(() => {
    listFiles()
      .then((response) => {
        setFiles(response.files);
        setListError(null);
      })
      .catch((error: unknown) => {
        setListError(error instanceof Error ? error.message : "加载文件列表失败");
      });
  }, []);

  useEffect(() => {
    refreshFiles();
  }, [refreshFiles]);

  const loadPath = useCallback((path: string) => {
    readFile(path)
      .then((response) => {
        blockedContent.current = null;
        editVersion.current = 0;
        setSaveState("idle");
        setConflictOpen(false);
        const loadedFile = {
          path: response.path,
          content: response.content,
          sha256: response.sha256,
          dirty: false,
        };
        latestOpenFile.current = loadedFile;
        setOpenFile(loadedFile);
      })
      .catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : "读取文件失败");
      });
  }, []);

  useEffect(() => {
    if (initialPath) loadPath(initialPath);
  }, [initialPath, loadPath]);

  const openPath = useCallback(
    (path: string) => {
      if (saving) {
        toast.info("正在自动保存，请稍后再切换文件");
        return;
      }
      if (openFile?.dirty) {
        setPendingSwitch(path);
        return;
      }
      loadPath(path);
    },
    [openFile, loadPath, saving],
  );

  const onSave = useCallback(async () => {
    if (!openFile || !openFile.dirty || saving) return;
    const request = openFile;
    if (
      request.content.trim() === "" &&
      requiredPromptFiles(getValues()).has(request.path)
    ) {
      blockedContent.current = request.content;
      setSaveState("invalid");
      return;
    }
    const saveVersion = editVersion.current;
    setSaving(true);
    setSaveState("saving");
    try {
      const result = await saveFile(
        request.path,
        request.content,
        request.sha256,
      );
      blockedContent.current = null;
      const latest = latestOpenFile.current;
      if (latest?.path === request.path) {
        const unchangedSinceRequest = editVersion.current === saveVersion;
        const updatedFile = {
          ...latest,
          sha256: result.sha256,
          dirty: !unchangedSinceRequest,
        };
        latestOpenFile.current = updatedFile;
        setOpenFile(updatedFile);
        setSaveState(unchangedSinceRequest ? "saved" : "editing");
      }
    } catch (error) {
      blockedContent.current = request.content;
      if (error instanceof ApiError && error.status === 409) {
        setSaveState("conflict");
        setConflictOpen(true);
      } else {
        setSaveState("error");
        toast.error(error instanceof Error ? error.message : "保存失败");
      }
    } finally {
      setSaving(false);
    }
  }, [openFile, saving, getValues]);

  useEffect(() => {
    if (
      !openFile?.dirty ||
      saving ||
      blockedContent.current === openFile.content
    ) {
      return;
    }
    setSaveState("editing");
    const timer = window.setTimeout(() => {
      void onSave();
    }, TEXT_AUTOSAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [openFile, saving, onSave]);

  const onCreate = useCallback(async () => {
    const path = newPath.trim();
    if (path === "") {
      toast.error("文件路径不能为空");
      return;
    }
    try {
      await saveFile(path, "", null);
      setCreateOpen(false);
      setNewPath("");
      refreshFiles();
      loadPath(path);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新建文件失败");
    }
  }, [newPath, refreshFiles, loadPath]);

  /** 冲突出口：拉取最新 sha 后用本地内容强制覆盖外部修改。 */
  const overwriteConflict = useCallback(async () => {
    const current = latestOpenFile.current;
    if (!current) return;
    try {
      const fresh = await readFile(current.path);
      const result = await saveFile(
        current.path,
        current.content,
        fresh.sha256,
      );
      blockedContent.current = null;
      const updatedFile = { ...current, sha256: result.sha256, dirty: false };
      latestOpenFile.current = updatedFile;
      setOpenFile(updatedFile);
      setSaveState("saved");
      setConflictOpen(false);
      toast.success("已用本地内容覆盖外部修改");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast.error("文件刚又被外部修改，请重新选择处理方式");
      } else {
        toast.error(error instanceof Error ? error.message : "覆盖失败");
      }
    }
  }, []);

  const reloadConflictFile = useCallback(() => {
    const current = latestOpenFile.current;
    if (!current) return;
    setConflictOpen(false);
    loadPath(current.path);
  }, [loadPath]);

  const copyConflictContent = useCallback(async () => {
    const current = latestOpenFile.current;
    if (!current) return;
    const ok = await copyText(current.content);
    if (ok) {
      toast.success("本地内容已复制到剪贴板");
    } else {
      toast.error("复制失败，请手动选择文本复制");
    }
  }, []);

  const retryCurrentFile = useCallback(() => {
    blockedContent.current = null;
    void onSave();
  }, [onSave]);

  const toggleGroup = useCallback((dir: string) => {
    setClosedGroups((current) => {
      const next = new Set(current);
      if (next.has(dir)) {
        next.delete(dir);
      } else {
        next.add(dir);
      }
      return next;
    });
  }, []);

  const renderFileButton = (file: string, label: string, indent: boolean) => {
    const Icon = fileIcon(file);
    return (
      <button
        key={file}
        type="button"
        title={file}
        onClick={() => openPath(file)}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left font-mono text-xs hover:bg-accent",
          indent && "pl-6",
          openFile?.path === file && "bg-accent font-medium",
        )}
      >
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="truncate">{label}</span>
      </button>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 md:flex-row">
      <div className="card-geek flex h-40 w-full shrink-0 flex-col rounded-sm border md:h-auto md:w-72">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium">配置目录文件</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setCreateOpen(true)}
          >
            新建
          </Button>
        </div>
        <ScrollArea className="flex-1">
          <div className="space-y-0.5 p-2">
            {listError ? (
              <p className="px-2 py-1 text-sm text-destructive">{listError}</p>
            ) : null}
            {fileGroups.map((group) =>
              group.dir === null ? (
                group.files.map((file) => renderFileButton(file, file, false))
              ) : (
                <div key={group.dir}>
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.dir ?? "")}
                    aria-expanded={!closedGroups.has(group.dir)}
                    className="flex w-full cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs font-medium text-muted-foreground hover:bg-accent"
                  >
                    {closedGroups.has(group.dir) ? (
                      <ChevronRight className="size-3.5" aria-hidden />
                    ) : (
                      <ChevronDown className="size-3.5" aria-hidden />
                    )}
                    <Folder className="size-3.5" aria-hidden />
                    {group.dir}/
                  </button>
                  {closedGroups.has(group.dir)
                    ? null
                    : group.files.map((file) =>
                        renderFileButton(
                          file,
                          file.slice((group.dir?.length ?? 0) + 1),
                          true,
                        ),
                      )}
                </div>
              ),
            )}
            {files.length === 0 && !listError ? (
              <p className="px-2 py-1 text-sm text-muted-foreground">
                config/ 下还没有 UTF-8 文本文件
              </p>
            ) : null}
          </div>
        </ScrollArea>
      </div>

      <div className="card-geek flex min-h-0 min-w-0 flex-1 flex-col rounded-sm border">
        {openFile ? (
          <>
            <div className="flex items-center justify-between gap-2 border-b px-4 py-2">
              <span className="truncate font-mono text-sm" title={openFile.path}>
                {openFile.path}
              </span>
              <div className="flex shrink-0 items-center gap-2">
                {isMarkdown(openFile.path) ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-pressed={previewOn}
                    aria-controls="markdown-live-preview"
                    onClick={() => setPreviewOn((current) => !current)}
                  >
                    {previewOn ? (
                      <EyeOff className="mr-1 h-4 w-4" />
                    ) : (
                      <Eye className="mr-1 h-4 w-4" />
                    )}
                    {previewOn ? "关闭预览" : "预览"}
                  </Button>
                ) : null}
                {saveState === "idle" ? (
                  <Badge variant="outline">自动保存已开启</Badge>
                ) : null}
                {saveState === "editing" ? (
                  <Badge variant="secondary">等待自动保存…</Badge>
                ) : null}
                {saveState === "saving" ? (
                  <Badge variant="secondary">自动保存中…</Badge>
                ) : null}
                {saveState === "saved" ? (
                  <Badge variant="outline">已自动保存</Badge>
                ) : null}
                {saveState === "invalid" ? (
                  <Badge variant="destructive">必填文件不能为空</Badge>
                ) : null}
                {saveState === "conflict" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    onClick={() => setConflictOpen(true)}
                  >
                    解决冲突
                  </Button>
                ) : null}
                {saveState === "error" ? (
                  <Button type="button" size="sm" onClick={retryCurrentFile}>
                    重试
                  </Button>
                ) : null}
              </div>
            </div>
            <div className="flex min-h-0 flex-1 flex-col md:flex-row">
              <div
                className={cn(
                  "min-h-0 min-w-0 flex-1 overflow-hidden",
                  previewOn &&
                    isMarkdown(openFile.path) &&
                    "border-b md:border-r md:border-b-0",
                )}
              >
                <Suspense
                  fallback={
                    <div
                      className="flex h-full items-center justify-center text-sm text-muted-foreground"
                      role="status"
                    >
                      正在加载编辑器…
                    </div>
                  }
                >
                  <MarkdownEditor
                    ariaLabel={`编辑 ${openFile.path}`}
                    language={isMarkdown(openFile.path) ? "markdown" : "text"}
                    value={openFile.content}
                    onChange={(content) => {
                      editVersion.current += 1;
                      if (blockedContent.current !== content) {
                        blockedContent.current = null;
                      }
                      setOpenFile((current) => {
                        if (!current) return current;
                        const updatedFile = { ...current, content, dirty: true };
                        latestOpenFile.current = updatedFile;
                        return updatedFile;
                      });
                    }}
                  />
                </Suspense>
              </div>
              {previewOn && isMarkdown(openFile.path) ? (
                <div
                  id="markdown-live-preview"
                  aria-label="Markdown 实时预览"
                  className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4"
                  role="region"
                >
                  <Suspense
                    fallback={
                      <p className="text-sm text-muted-foreground" role="status">
                        正在生成预览…
                      </p>
                    }
                  >
                    <MarkdownPreview content={openFile.content} />
                  </Suspense>
                </div>
              ) : null}
            </div>
          </>
        ) : (
          <EmptyState
            icon={FileText}
            title="未选择文件"
            description="从左侧选择要编辑的配置文本文件，或点击「新建」创建。"
            className="flex-1"
          />
        )}
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建文本文件</DialogTitle>
            <DialogDescription>
              路径相对 config/ 目录，父目录必须已存在。
            </DialogDescription>
          </DialogHeader>
          <Input
            aria-label="新文本文件路径"
            placeholder="如 ai_group_chat/prompts/roles/new-role.md"
            value={newPath}
            onChange={(event) => setNewPath(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void onCreate();
              }
            }}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button onClick={() => void onCreate()}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={conflictOpen} onOpenChange={setConflictOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>文件已被外部修改</DialogTitle>
            <DialogDescription>
              自动保存发现 {openFile?.path ?? "该文件"}{" "}
              已被其他方式修改，当前本地修改尚未保存。请选择处理方式。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-wrap">
            <Button
              type="button"
              variant="outline"
              onClick={() => void copyConflictContent()}
            >
              复制本地内容
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={reloadConflictFile}
            >
              丢弃本地并重新载入
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void overwriteConflict()}
            >
              用本地内容覆盖
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={pendingSwitch !== null}
        onOpenChange={(open) => {
          if (!open) setPendingSwitch(null);
        }}
        title="切换文件将丢失未保存修改"
        description="当前文件有尚未自动保存的修改（可能是校验失败或冲突未解决），切换后将丢失。"
        confirmLabel="仍然切换"
        destructive
        onConfirm={() => {
          if (pendingSwitch !== null) loadPath(pendingSwitch);
        }}
      />
    </div>
  );
}
