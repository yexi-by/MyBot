import { ArrowDown, ArrowUp, BookOpen, FilePlus, FileText, Library, Minus, UserRound } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { FileEditor } from "../components/FileEditor";
import { Menu } from "../components/Menu";
import { Badge, Button, EmptyState } from "../components/primitives";
import { DetailHeader, MasterDetail, PageHeader } from "../components/shell";
import { ApiError, errorMessage, put } from "../lib/api";
import { change, useConfig } from "../lib/config";
import { linkHandler, navigate } from "../lib/navigation";
import { filesResource, useResource } from "../lib/resource";
import { list } from "../lib/types";
import { prompt, toast, useMediaQuery } from "../lib/ui";

const enc = encodeURIComponent;
const isText = (path: string) => /\.(md|txt)$/i.test(path);

async function createFile(existing: string[]) {
  const path = await prompt({
    title: "新建文本文件",
    message: "文件保存在配置目录中，可用作人格或知识库。",
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
      error instanceof ApiError && error.status === 409 ? "已有同名文件" : "创建失败：" + errorMessage(error),
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
  actions,
}: {
  path: string;
  selected: boolean;
  icon: LucideIcon;
  sub?: string;
  actions?: ReactNode;
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
      {actions && <span className="md-item-actions">{actions}</span>}
    </li>
  );
}

export function PromptsPage({ path }: { path: string }) {
  const state = useConfig();
  const files = useResource(filesResource);
  const wide = useMediaQuery("(min-width: 768px)");
  const persona = String(state.draft.persona_file || "");
  const knowledge = list(state.draft.knowledge_files).map(String);
  const texts = (files.data ?? []).filter(isText);
  const others = texts.filter((file) => file !== persona && !knowledge.includes(file));
  const selected = path || (wide ? persona : "");
  const setKnowledge = (next: string[]) => change(["knowledge_files"], next);
  const move = (index: number, step: number) => {
    const next = [...knowledge];
    const [item] = next.splice(index, 1);
    next.splice(index + step, 0, item);
    setKnowledge(next);
  };
  const role = !selected
    ? null
    : selected === persona
      ? "persona"
      : knowledge.includes(selected)
        ? "knowledge"
        : "unused";

  return (
    <div className="page">
      <PageHeader
        title="提示词"
        description="人格决定 Agent 的表达方式，知识库作为背景资料随每轮对话发送"
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
            <li className="md-section">人格</li>
            {persona ? (
              <FileItem path={persona} selected={selected === persona} icon={UserRound} sub="每轮对话都会发送" />
            ) : (
              <li className="md-empty muted">还没有选择人格文件</li>
            )}
            <li className="md-section">知识库 · {knowledge.length}</li>
            {knowledge.length ? (
              knowledge.map((file, index) => (
                <FileItem
                  key={file}
                  path={file}
                  selected={selected === file}
                  icon={BookOpen}
                  sub={"第 " + (index + 1) + " 份"}
                  actions={
                    <Menu
                      label={file + " 的操作"}
                      items={[
                        { label: "上移", icon: ArrowUp, disabled: index === 0, onSelect: () => move(index, -1) },
                        {
                          label: "下移",
                          icon: ArrowDown,
                          disabled: index === knowledge.length - 1,
                          onSelect: () => move(index, 1),
                        },
                        {
                          label: "移出知识库",
                          icon: Minus,
                          onSelect: () => {
                            setKnowledge(knowledge.filter((item) => item !== file));
                            toast("已移出知识库，文件仍保留");
                          },
                        },
                      ]}
                    />
                  }
                />
              ))
            ) : (
              <li className="md-empty muted">没有知识库文件</li>
            )}
            <li className="md-section">其他文件 · {others.length}</li>
            {others.map((file) => (
              <FileItem
                key={file}
                path={file}
                selected={selected === file}
                icon={FileText}
                sub="未使用"
                actions={
                  <Menu
                    label={file + " 的操作"}
                    items={[
                      { label: "加入知识库", icon: Library, onSelect: () => setKnowledge([...knowledge, file]) },
                      { label: "设为人格", icon: UserRound, onSelect: () => change(["persona_file"], file) },
                    ]}
                  />
                }
              />
            ))}
            {!others.length && <li className="md-empty muted">配置目录中没有其他 .md 或 .txt 文件</li>}
          </ul>
        }
      >
        {selected ? (
          <div className="detail detail-editor">
            <DetailHeader
              back={{ to: "/prompts", label: "全部文件" }}
              title={selected}
              badges={
                role === "persona" ? (
                  <Badge tone="accent">人格</Badge>
                ) : role === "knowledge" ? (
                  <Badge tone="accent">知识库</Badge>
                ) : (
                  <Badge>未使用</Badge>
                )
              }
              actions={
                role === "unused" && (
                  <>
                    <Button size="sm" onClick={() => setKnowledge([...knowledge, selected])}>
                      加入知识库
                    </Button>
                    <Button size="sm" onClick={() => change(["persona_file"], selected)}>
                      设为人格
                    </Button>
                  </>
                )
              }
            />
            <FileEditor
              key={selected}
              path={selected}
              showPath={false}
              kind={selected.toLowerCase().endsWith(".md") ? "markdown" : "text"}
              note={
                role === "unused"
                  ? "这个文件目前不会发送给模型。"
                  : "保存后从下一轮对话开始生效。" + (role === "persona" ? "人格不能为空。" : "")
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
