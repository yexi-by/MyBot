import { BookOpen, FilePlus, FileText, UserRound } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMemo } from "react";
import { FileEditor } from "../components/FileEditor";
import { Badge, Button, EmptyState } from "../components/primitives";
import { DetailHeader, MasterDetail, PageHeader } from "../components/shell";
import { ApiError, errorMessage, put } from "../lib/api";
import { useConfig } from "../lib/config";
import { promptUsage } from "../lib/models";
import { linkHandler, navigate } from "../lib/navigation";
import { filesResource, useGroupNames, useResource } from "../lib/resource";
import { prompt, toast, useMediaQuery } from "../lib/ui";
import { conversationEntries } from "../schema/fields";

const enc = encodeURIComponent;
const isText = (path: string) => /\.(md|txt)$/i.test(path);

async function createFile(existing: string[]) {
  const path = await prompt({
    title: "新建文本文件",
    message: "文件保存在配置目录中，可在任意会话中选作人格或知识库。",
    label: "文件名",
    placeholder: "例如 knowledge/faq.md",
    initial: ".md",
    confirmLabel: "创建",
    validate: (value) => {
      const parts = value.split("/");
      const leaf = parts.at(-1) ?? "";
      if (!/^[^/]+\.(md|txt)$/i.test(leaf)) return "请填写文件名，并以 .md 或 .txt 结尾";
      if (
        value.startsWith("/") ||
        value.includes(":") ||
        value.includes("\\") ||
        parts.some((part) => !part || part === "." || part === "..")
      ) {
        return "只能使用配置目录内的相对路径，用 / 分隔子目录";
      }
      if (existing.includes(value)) return "已有同名文件";
      return "";
    },
  });
  if (!path) return;
  try {
    await put("/api/files", { path, content: "", sha: null });
  } catch (error) {
    toast(
      error instanceof ApiError && error.status === 409
        ? "已有同名文件"
        : "创建失败：" + errorMessage(error),
      "danger",
    );
    return;
  }
  await filesResource.refresh();
  void navigate("/prompts?path=" + enc(path));
  toast("已创建 " + path, "success");
}

function FileItem({
  path,
  selected,
  icon: Icon,
  sub,
}: {
  path: string;
  selected: boolean;
  icon: LucideIcon;
  sub?: string;
}) {
  const to = "/prompts?path=" + enc(path);
  return (
    <li className="md-item-row">
      <a
        href={to}
        className={"md-item" + (selected ? " active" : "")}
        aria-current={selected ? "page" : undefined}
        onClick={linkHandler(to)}
      >
        <Icon size={16} className="md-item-icon" aria-hidden="true" />
        <span className="md-item-text">
          <span className="md-item-title">{path}</span>
          {sub && <span className="md-item-sub">{sub}</span>}
        </span>
      </a>
    </li>
  );
}

export function PromptsPage({ path }: { path: string }) {
  const state = useConfig();
  const groups = useGroupNames();
  const files = useResource(filesResource);
  const wide = useMediaQuery("(min-width: 768px)");

  const allFiles = useMemo(() => {
    const names = new Set<string>((files.data ?? []).filter(isText));
    for (const [, conv] of conversationEntries(state.draft)) {
      if (typeof conv.persona_file === "string" && conv.persona_file.trim())
        names.add(conv.persona_file.trim());
      if (Array.isArray(conv.knowledge_files))
        for (const item of conv.knowledge_files)
          if (typeof item === "string" && item.trim()) names.add(item.trim());
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [files.data, state.draft]);

  const usageMap = useMemo(() => {
    const map = new Map<string, ReturnType<typeof promptUsage>>();
    for (const file of allFiles) {
      map.set(file, promptUsage(state.draft, file, groups));
    }
    return map;
  }, [allFiles, state.draft, groups]);

  const inUseFiles = allFiles.filter((file) => (usageMap.get(file)?.length ?? 0) > 0);
  const unusedFiles = allFiles.filter((file) => (usageMap.get(file)?.length ?? 0) === 0);
  const defaultFile = inUseFiles[0] ?? allFiles[0] ?? "";
  const selected = path || (wide ? defaultFile : "");
  const uses = selected ? (usageMap.get(selected) ?? promptUsage(state.draft, selected, groups)) : [];
  const usedAsPersona = uses.some((use) => use.parts.includes("人格"));
  const usedAsKnowledge = uses.some((use) => use.parts.includes("知识库"));

  return (
    <div className="page">
      <PageHeader
        title="提示词"
        description="管理配置目录中的人格提示词与知识库文件；各会话使用的文件在「会话」页面中单独绑定。"
        actions={
          <Button icon={FilePlus} onClick={() => void createFile(files.data ?? [])}>
            新建文件
          </Button>
        }
      />
      <MasterDetail
        label="提示词文件"
        hasDetail={!!path}
        list={
          <ul className="md-items">
            <li className="md-section">会话使用中 · {inUseFiles.length}</li>
            {inUseFiles.length ? (
              inUseFiles.map((file) => {
                const fileUses = usageMap.get(file) ?? [];
                const isPersona = fileUses.some((use) => use.parts.includes("人格"));
                const roles = [
                  ...(isPersona ? ["人格"] : []),
                  ...(fileUses.some((use) => use.parts.includes("知识库")) ? ["知识库"] : []),
                ].join(" / ");
                return (
                  <FileItem
                    key={file}
                    path={file}
                    selected={selected === file}
                    icon={isPersona ? UserRound : BookOpen}
                    sub={roles + " · " + fileUses.length + " 个会话"}
                  />
                );
              })
            ) : (
              <li className="md-empty muted">暂无会话绑定提示词文件</li>
            )}
            <li className="md-section">未绑定文件 · {unusedFiles.length}</li>
            {unusedFiles.map((file) => (
              <FileItem
                key={file}
                path={file}
                selected={selected === file}
                icon={FileText}
                sub="可在会话中选择使用"
              />
            ))}
            {!unusedFiles.length && (
              <li className="md-empty muted">配置目录中没有其他未绑定的 .md 或 .txt 文件</li>
            )}
          </ul>
        }
      >
        {selected ? (
          <div className="detail detail-editor">
            <div className="detail-sticky">
              <DetailHeader
                back={{ to: "/prompts", label: "全部文件" }}
                title={selected}
                subtitle={
                  uses.length ? (
                    <span className="row wrap">
                      <span className="muted">使用会话：</span>
                      {uses.map((use) => (
                        <a
                          key={use.base.join(":")}
                          href={use.route}
                          className="chip"
                          onClick={linkHandler(use.route)}
                        >
                          {use.label}（{use.parts.join("、")}）
                        </a>
                      ))}
                    </span>
                  ) : (
                    "当前没有会话绑定此文件，可在「会话」页面的「提示词与知识库」中选择使用"
                  )
                }
                badges={
                  <>
                    {usedAsPersona && <Badge tone="accent">人格</Badge>}
                    {usedAsKnowledge && <Badge tone="accent">知识库</Badge>}
                    {!usedAsPersona && !usedAsKnowledge && <Badge>未绑定</Badge>}
                  </>
                }
              />
            </div>
            <FileEditor
              key={selected}
              path={selected}
              showPath={false}
              kind={selected.toLowerCase().endsWith(".md") ? "markdown" : "text"}
              note={
                !uses.length
                  ? "这个文件目前未被任何会话绑定。"
                  : "保存后从下一轮对话开始生效。" + (usedAsPersona ? "被选作人格的文件不能为空。" : "")
              }
            />
          </div>
        ) : (
          <EmptyState icon={FileText} title="选择一个文件">
            在左侧选择人格或知识库文件进行编辑。
          </EmptyState>
        )}
      </MasterDetail>
    </div>
  );
}
