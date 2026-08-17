/** 配置文本编辑页：config/ 内 prompt、知识库等 md/txt 文件的自动保存编辑器。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useFormContext } from "react-hook-form";
import { toast } from "sonner";

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
import { Textarea } from "@/components/ui/textarea";
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

export default function FilesPage() {
  const [files, setFiles] = useState<string[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<OpenFile | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<FileSaveState>("idle");
  const blockedContent = useRef<string | null>(null);
  const latestOpenFile = useRef<OpenFile | null>(null);
  const editVersion = useRef(0);
  const { getValues } = useFormContext<MyBotConfigData>();

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

  const openPath = useCallback(
    (path: string) => {
      if (saving) {
        toast.info("正在自动保存，请稍后再切换文件");
        return;
      }
      if (openFile?.dirty) {
        const confirmed = window.confirm("当前文件尚未自动保存，切换后将丢失，继续？");
        if (!confirmed) return;
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
        toast.error("文件已被外部修改，请重新载入后再编辑");
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
    const lowerPath = path.toLowerCase();
    if (!lowerPath.endsWith(".md") && !lowerPath.endsWith(".txt")) {
      toast.error("只支持 .md 或 .txt 文件");
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

  const reloadCurrentFile = useCallback(() => {
    const current = latestOpenFile.current;
    if (!current) return;
    const confirmed = window.confirm("重新载入会丢失当前本地修改，继续？");
    if (confirmed) {
      loadPath(current.path);
    }
  }, [loadPath]);

  const retryCurrentFile = useCallback(() => {
    blockedContent.current = null;
    void onSave();
  }, [onSave]);

  return (
    <div className="flex h-[calc(100dvh-10.5rem)] min-h-0 flex-col gap-4 md:h-[calc(100dvh-7.5rem)] md:flex-row">
      <div className="flex h-40 w-full shrink-0 flex-col rounded-lg border md:h-auto md:w-64">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium">文本文件</span>
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
            {files.map((file) => (
              <button
                key={file}
                type="button"
                title={file}
                onClick={() => openPath(file)}
                className={cn(
                  "w-full rounded-md px-2 py-1.5 text-left font-mono text-xs break-all hover:bg-accent",
                  openFile?.path === file && "bg-accent",
                )}
              >
                {file}
              </button>
            ))}
            {files.length === 0 && !listError ? (
              <p className="px-2 py-1 text-sm text-muted-foreground">
                config/ 下还没有文本文件
              </p>
            ) : null}
          </div>
        </ScrollArea>
      </div>

      <div className="flex min-w-0 flex-1 flex-col rounded-lg border">
        {openFile ? (
          <>
            <div className="flex items-center justify-between border-b px-4 py-2">
              <span className="font-mono text-sm">{openFile.path}</span>
              <div className="flex items-center gap-2">
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
                  <Button type="button" size="sm" onClick={reloadCurrentFile}>
                    重新载入
                  </Button>
                ) : null}
                {saveState === "error" ? (
                  <Button type="button" size="sm" onClick={retryCurrentFile}>
                    重试
                  </Button>
                ) : null}
              </div>
            </div>
            <Textarea
              aria-label={`编辑 ${openFile.path}`}
              className="min-h-0 flex-1 resize-none rounded-none border-0 font-mono text-sm focus-visible:ring-0"
              value={openFile.content}
              onChange={(event) => {
                const content = event.target.value;
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
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            从左侧选择要编辑的 prompt 或知识库文件
          </div>
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
    </div>
  );
}
