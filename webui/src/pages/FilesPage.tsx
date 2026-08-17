/** 配置文本编辑页：config/ 内 prompt、知识库等 md/txt 文件的在线编辑。 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

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
import { cn } from "@/lib/utils";

interface OpenFile {
  path: string;
  content: string;
  sha256: string;
  dirty: boolean;
}

export default function FilesPage() {
  const [files, setFiles] = useState<string[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<OpenFile | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [saving, setSaving] = useState(false);

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

  const openPath = useCallback(
    (path: string) => {
      if (openFile?.dirty) {
        const confirmed = window.confirm("当前文件有未保存修改，切换后将丢失，继续？");
        if (!confirmed) return;
      }
      readFile(path)
        .then((response) => {
          setOpenFile({
            path: response.path,
            content: response.content,
            sha256: response.sha256,
            dirty: false,
          });
        })
        .catch((error: unknown) => {
          toast.error(error instanceof Error ? error.message : "读取文件失败");
        });
    },
    [openFile],
  );

  const onSave = useCallback(async () => {
    if (!openFile) return;
    setSaving(true);
    try {
      const result = await saveFile(openFile.path, openFile.content, openFile.sha256);
      setOpenFile({ ...openFile, sha256: result.sha256, dirty: false });
      toast.success("文件已保存，热载会自动应用");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const confirmed = window.confirm("文件已被外部修改，是否丢弃本地修改并刷新？");
        if (confirmed) {
          openPath(openFile.path);
        }
      } else {
        toast.error(error instanceof Error ? error.message : "保存失败");
      }
    } finally {
      setSaving(false);
    }
  }, [openFile, openPath]);

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
      openPath(path);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新建文件失败");
    }
  }, [newPath, refreshFiles, openPath]);

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
              <Button
                type="button"
                size="sm"
                disabled={!openFile.dirty || saving}
                onClick={onSave}
              >
                {saving ? "保存中…" : "保存"}
              </Button>
            </div>
            <Textarea
              aria-label={`编辑 ${openFile.path}`}
              className="min-h-0 flex-1 resize-none rounded-none border-0 font-mono text-sm focus-visible:ring-0"
              value={openFile.content}
              onChange={(event) =>
                setOpenFile({ ...openFile, content: event.target.value, dirty: true })
              }
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
