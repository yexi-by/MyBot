import { useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  ExternalLink,
  Play,
  Plug,
  Plus,
  Trash2,
  Wrench,
} from "lucide-react";
import { Combobox } from "../components/Combobox";
import { ConfigSection, FieldShell } from "../components/form";
import { Menu } from "../components/Menu";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  IconButton,
  Segmented,
  Spinner,
  StatusDot,
  Switch,
  Tabs,
} from "../components/primitives";
import { DetailHeader, MasterDetail, QQAvatar, SearchInput } from "../components/shell";
import { ToolDrawer } from "../components/ToolDrawer";
import { errorMessage, post } from "../lib/api";
import { change, getConfig, save, useConfig } from "../lib/config";
import { accessScope } from "../lib/conversations";
import { formatBytes } from "../lib/format";
import { coverageState, mcpState, toolScopes } from "../lib/labels";
import { linkHandler, navigate } from "../lib/navigation";
import {
  accessResource,
  coverageResource,
  runtimeResource,
  toolsResource,
  useGroupNames,
  useResource,
} from "../lib/resource";
import { getAt, object } from "../lib/types";
import type { Tool } from "../lib/types";
import { confirm, prompt, toast, useMediaQuery } from "../lib/ui";
import type { Route } from "../routes";
import {
  defaultMcp,
  imageConnectionSection,
  imageRequestSection,
  mcpConnectionSection,
  mcpSecretsSection,
} from "../schema/fields";

type ToolsRoute = Extract<Route, { page: "tools" }>;
const enc = encodeURIComponent;

type Kind = "all" | "open" | "closed";
const FIRST_PAGE = 25;
const NEXT_PAGE = 50;

function Catalog({ selected }: { selected: string }) {
  const state = useConfig();
  const tools = useResource(toolsResource);
  const access = useResource(accessResource);
  const groups = useGroupNames();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [kind, setKind] = useState<Kind>("all");
  const [limit, setLimit] = useState(FIRST_PAGE);
  // “已开放于”按已保存的配置计算，配置保存后刷新。
  useEffect(() => {
    void accessResource.refresh();
  }, [state.sha]);
  // 与能力权限相同，按分类顺序排列。
  const all = useMemo(() => {
    const order = new Map((tools.data?.categories ?? []).map((item, index) => [item.id, index]));
    return (tools.data?.tools ?? [])
      .map((tool, index) => ({ tool, index }))
      .sort(
        (a, b) =>
          (order.get(a.tool.category) ?? order.size) - (order.get(b.tool.category) ?? order.size) ||
          a.index - b.index,
      )
      .map((item) => item.tool);
  }, [tools.data]);
  const catalog = useMemo(() => new Map((tools.data?.tools ?? []).map((tool) => [tool.name, tool])), [tools.data]);
  const labels = useMemo(
    () => new Map((tools.data?.categories ?? []).map((item) => [item.id, item.label])),
    [tools.data],
  );
  const openIn = useMemo(() => {
    const index = new Map<string, string[]>();
    for (const [key, names] of Object.entries(access.data?.scopes ?? {}))
      for (const name of names) index.set(name, [...(index.get(name) ?? []), key]);
    return index;
  }, [access.data]);
  // 开放给所有已启用会话的工具只显示一枚“全部”标签，避免每行重复同一串群名。
  const enabledScopes = useMemo(() => {
    const keys = Object.entries(object(state.draft.groups))
      .filter(([, group]) => object(group).enabled === true)
      .map(([id]) => "group:" + id);
    if (object(state.draft.private).enabled === true) keys.push("private");
    return keys;
  }, [state.draft.groups, state.draft.private]);
  const scopeCount = enabledScopes.length;
  const openToAll = (scopes: string[]) =>
    scopeCount > 1 && enabledScopes.every((key) => scopes.includes(key));
  if (!tools.data)
    return tools.error ? (
      <Banner tone="danger" icon={CircleAlert} title="无法读取工具目录">
        {tools.error}
      </Banner>
    ) : (
      <Spinner label="正在读取工具目录" />
    );
  const text = query.trim().toLowerCase();
  const visible = all.filter(
    (tool) =>
      (!category || tool.category === category) &&
      (kind !== "open" || (openIn.get(tool.name)?.length ?? 0) > 0) &&
      (kind !== "closed" || !(openIn.get(tool.name)?.length ?? 0)) &&
      (!text || (tool.title + " " + tool.name + " " + tool.description).toLowerCase().includes(text)),
  );
  const open = (name: string | null) =>
    void navigate(name ? "/tools?tool=" + enc(name) : "/tools", { replace: true, keepScroll: true });
  const current: Tool | null = selected ? (catalog.get(selected) ?? null) : null;
  const shown = visible.slice(0, limit);
  const rest = visible.length - shown.length;
  return (
    <div className="stack">
      <section className="card">
        <header className="card-header sticky wrap">
          <SearchInput
            value={query}
            onChange={(next) => {
              setQuery(next);
              setLimit(FIRST_PAGE);
            }}
            placeholder="搜索工具名称或说明"
            label="搜索工具"
          />
          <Combobox
            className="toolbar-select"
            label="分类"
            value={category}
            onChange={(next) => {
              setCategory(next);
              setLimit(FIRST_PAGE);
            }}
            options={[
              { value: "", label: "全部分类" },
              ...(tools.data.categories ?? []).map((item) => ({ value: item.id, label: item.label })),
            ]}
          />
          <Segmented
            size="sm"
            label="开放状态"
            value={kind}
            onChange={(next) => {
              setKind(next);
              setLimit(FIRST_PAGE);
            }}
            options={[
              { value: "all", label: "全部" },
              { value: "open", label: "已开放" },
              { value: "closed", label: "未开放" },
            ]}
          />
          <span className="grow" />
          <span className="muted small num">
            {visible.length} / {all.length} 个工具
          </span>
        </header>
        {visible.length ? (
          <>
            <div className="table-wrap">
              <table className="table table-hover">
                <thead>
                  <tr>
                    <th>工具</th>
                    <th className="hide-narrow">分类</th>
                    <th className="hide-narrow">适用范围</th>
                    <th>已开放于</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((tool) => {
                    const scopes = openIn.get(tool.name) ?? [];
                    return (
                      <tr key={tool.name} onClick={() => open(tool.name)}>
                        <td>
                          <button
                            type="button"
                            className="cell-title"
                            onClick={(event) => {
                              event.stopPropagation();
                              open(tool.name);
                            }}
                          >
                            {tool.title}
                          </button>
                          <div className="cell-sub">
                            <span className="mono">{tool.name}</span>
                          </div>
                        </td>
                        <td className="hide-narrow secondary">{labels.get(tool.category) ?? tool.category}</td>
                        <td className="hide-narrow secondary nowrap">{toolScopes[tool.scope]}</td>
                        <td>
                          {openToAll(scopes) ? (
                            <span className="chip chip-all" title={scopes.map((key) => accessScope(key, groups).label).join("、")}>
                              全部 {scopeCount} 个会话
                            </span>
                          ) : scopes.length ? (
                            <span className="chips">
                              {scopes.slice(0, 3).map((key) => (
                                <span className="chip" key={key}>
                                  {accessScope(key, groups).label}
                                </span>
                              ))}
                              {scopes.length > 3 && <span className="muted small">等 {scopes.length} 个</span>}
                            </span>
                          ) : (
                            <span className="muted small">未开放</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {(rest > 0 || limit > FIRST_PAGE) && (
              <footer className="card-footer">
                <span className="muted small num">
                  已显示 {shown.length} / {visible.length} 个工具
                </span>
                <span className="grow" />
                {limit > FIRST_PAGE && (
                  <Button size="sm" variant="ghost" icon={ChevronUp} onClick={() => setLimit(FIRST_PAGE)}>
                    收起
                  </Button>
                )}
                {rest > NEXT_PAGE && (
                  <Button size="sm" variant="ghost" onClick={() => setLimit(visible.length)}>
                    显示全部
                  </Button>
                )}
                {rest > 0 && (
                  <Button size="sm" icon={ChevronDown} onClick={() => setLimit((value) => value + NEXT_PAGE)}>
                    再显示 {Math.min(rest, NEXT_PAGE)} 个
                  </Button>
                )}
              </footer>
            )}
          </>
        ) : (
          <EmptyState icon={Wrench} title="没有符合条件的工具" compact>
            调整搜索词或筛选条件后再试。
          </EmptyState>
        )}
      </section>
      <ToolDrawer
        tool={current}
        categoryLabel={current ? (labels.get(current.category) ?? current.category) : ""}
        onClose={() => open(null)}
        openIn={current ? (openIn.get(current.name) ?? []) : undefined}
        catalog={catalog}
      />
    </div>
  );
}

const namePattern = /^[A-Za-z0-9_-]{1,64}$/;
async function addServer(existing: Record<string, unknown>) {
  const name = await prompt({
    title: "添加 MCP 服务",
    message: "名称会成为工具名的一部分（mcp__名称__工具），创建后不能修改。",
    label: "服务名称",
    placeholder: "例如 search",
    confirmLabel: "添加",
    validate: (value) =>
      !namePattern.test(value)
        ? "只能使用字母、数字、短横线和下划线，最多 64 个字符"
        : Object.hasOwn(existing, value)
          ? "已有同名服务"
          : "",
  });
  if (!name) return;
  change(["mcp", name], defaultMcp());
  void navigate("/tools/mcp/" + enc(name));
}

function McpDetail({
  name,
  onAdd,
  subnav,
}: {
  name: string;
  onAdd: () => void;
  subnav?: React.ReactNode;
}) {
  const state = useConfig();
  const runtime = useResource(runtimeResource, 5000);
  const tools = useResource(toolsResource);
  const base = ["mcp", name];
  const raw = getAt(state.draft, base);
  const prefix = "mcp__" + name + "__";
  const discovered = useMemo(
    () =>
      (tools.data?.tools ?? [])
        .filter((tool) => tool.category === "mcp." + name)
        .map((tool) => ({ value: tool.name.slice(prefix.length), label: tool.title })),
    [tools.data, name, prefix],
  );
  if (raw === undefined)
    return (
      <div className="detail">
        <div className="detail-sticky">
          <DetailHeader back={{ to: "/tools/mcp", label: "全部 MCP 服务" }} title={name} save />
          {subnav}
        </div>
        <div className="detail-body">
          <EmptyState
            icon={CircleAlert}
            title="MCP 服务不存在"
            action={<Button onClick={() => void navigate("/tools/mcp")}>返回列表</Button>}
          >
            “{name}”可能已被删除。
          </EmptyState>
        </div>
      </div>
    );
  const server = object(raw);
  const enabled = server.enabled === true;
  const status = runtime.data?.mcp[name];
  const label = mcpState(status?.state);
  return (
    <div className="detail">
      <div className="detail-sticky">
        <DetailHeader
          back={{ to: "/tools/mcp", label: "全部 MCP 服务" }}
          avatar={<QQAvatar kind="icon" icon={Plug} name={name} size="md" />}
          title={name}
          save
          badges={<Badge tone={label.tone}>{label.text}</Badge>}
          subtitle={
            String(server.transport) === "stdio"
              ? String(server.command || "未填写启动命令")
              : String(server.url || "未填写服务地址")
          }
          actions={
            <>
              <Button size="sm" icon={Plus} onClick={onAdd}>
                添加 MCP 服务
              </Button>
              <label className="switch-label">
                <span>{enabled ? "已启用" : "未启用"}</span>
                <Switch
                  checked={enabled}
                  label={"启用 " + name}
                  onChange={async (next) => {
                    if (
                      !next &&
                      !(await confirm({
                        title: "停用 " + name + "？",
                        message: "重启 Harness 后，这个服务提供的工具将不再可用。",
                        confirmLabel: "停用",
                        danger: true,
                      }))
                    )
                      return;
                    change([...base, "enabled"], next);
                  }}
                />
              </label>
              <Menu
                items={[
                  {
                    label: "删除",
                    icon: Trash2,
                    danger: true,
                    onSelect: async () => {
                      const accepted = await confirm({
                        title: "删除 MCP 服务 " + name + "？",
                        message: "连接设置、请求头和环境变量会一并删除。会话中针对这些工具的单独设置会保留，但不再生效。",
                        confirmLabel: "删除",
                        danger: true,
                      });
                      if (!accepted) return;
                      change(base, undefined);
                      void navigate("/tools/mcp", { replace: true });
                      toast("已删除 MCP 服务 " + name);
                    },
                  },
                ]}
              />
            </>
          }
        />
        {subnav}
      </div>
      <div className="detail-body stack">
        {status && (status.message || status.error_type) ? (
          <Banner tone={label.tone === "success" ? "neutral" : "warning"} icon={CircleAlert} title={label.text}>
            {status.message || status.error_type}
          </Banner>
        ) : null}
        {status?.state === "connected" && (
          <p className="muted small">已发现 {status.tools ?? discovered.length} 个工具，可在会话的能力权限中开放。</p>
        )}
        <ConfigSection def={mcpConnectionSection} base={base} />
        <ConfigSection def={mcpSecretsSection} base={base} />
        {!!status?.excluded_tools?.length && (
          <Banner tone="warning" title="部分工具未加载">
            {status.excluded_tools.map((item) => (
              <p key={item.name}>
                {item.name}：{item.reason}
              </p>
            ))}
          </Banner>
        )}
      </div>
    </div>
  );
}

function sourceLink(base: string, source: string | undefined) {
  if (!source) return "";
  const match = /^(https:\/\/github\.com\/[^/]+\/[^/]+)\/tree\/([^/]+)\//.exec(base);
  return match ? match[1] + "/blob/" + match[2] + "/" + source : "";
}

function Coverage() {
  const coverage = useResource(coverageResource);
  const tools = useResource(toolsResource);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [limit, setLimit] = useState(FIRST_PAGE);
  const titles = useMemo(() => new Map((tools.data?.tools ?? []).map((tool) => [tool.name, tool.title])), [tools.data]);
  const data = coverage.data;
  if (!data)
    return coverage.error ? (
      <Banner tone="danger" icon={CircleAlert} title="无法读取覆盖清单">
        {coverage.error}
      </Banner>
    ) : (
      <Spinner label="正在读取覆盖清单" />
    );
  const present = new Set(data.actions.map((action) => action.status));
  const statuses = [
    ...["implemented", "adapted", "alias", "internal", "excluded"].filter((value) => present.has(value)),
    ...[...present].filter((value) => !["implemented", "adapted", "alias", "internal", "excluded"].includes(value)),
  ];
  const text = query.trim().toLowerCase();
  const visible = data.actions.filter(
    (action) =>
      (status === "all" || action.status === status) &&
      (!text ||
        (action.action + " " + (action.tool ?? "") + " " + (action.tool ? (titles.get(action.tool) ?? "") : "") + " " + action.reason)
          .toLowerCase()
          .includes(text)),
  );
  const shown = visible.slice(0, limit);
  const rest = visible.length - shown.length;
  return (
    <div className="stack">
      <p className="secondary small">
        对照 NapCat {data.version}（{data.commit.slice(0, 7)}）的全部 OneBot 动作，说明每个动作是否作为工具提供。
        <a href={data.source_url} target="_blank" rel="noreferrer" className="inline-link">
          上游源码 <ExternalLink size={13} aria-hidden="true" />
        </a>
      </p>
      {data.aliases && <p className="muted small">{data.aliases}</p>}
      <section className="card">
        <header className="card-header sticky wrap">
          <SearchInput
            value={query}
            onChange={(next) => {
              setQuery(next);
              setLimit(FIRST_PAGE);
            }}
            placeholder="搜索动作或工具"
            label="搜索覆盖清单"
          />
          <Segmented
            size="sm"
            label="覆盖状态"
            value={status}
            onChange={(next) => {
              setStatus(next);
              setLimit(FIRST_PAGE);
            }}
            options={[
              { value: "all", label: "全部", count: data.actions.length },
              ...statuses.map((value) => ({
                value,
                label: coverageState(value).text,
                count: data.actions.filter((action) => action.status === value).length,
              })),
            ]}
          />
        </header>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>动作</th>
                <th>状态</th>
                <th>对应工具</th>
                <th className="hide-narrow">说明</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((action) => {
                const state = coverageState(action.status);
                const link = sourceLink(data.source_url, action.source);
                return (
                  <tr key={action.action}>
                    <td className="nowrap">
                      {link ? (
                        <a href={link} target="_blank" rel="noreferrer" className="mono plain-link" title="查看上游源码">
                          {action.action}
                        </a>
                      ) : (
                        <span className="mono">{action.action}</span>
                      )}
                    </td>
                    <td>
                      <Badge tone={state.tone}>{state.text}</Badge>
                    </td>
                    <td>
                      {action.tool ? (
                        <a href={"/tools?tool=" + enc(action.tool)} onClick={linkHandler("/tools?tool=" + enc(action.tool))}>
                          {titles.get(action.tool) ?? action.tool}
                        </a>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="hide-narrow secondary">
                      {action.tool && action.reason === titles.get(action.tool) ? "" : action.reason}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!visible.length && <p className="table-empty muted">没有符合条件的动作</p>}
        {(rest > 0 || limit > FIRST_PAGE) && (
          <footer className="card-footer">
            <span className="muted small num">
              已显示 {shown.length} / {visible.length} 个动作
            </span>
            <span className="grow" />
            {limit > FIRST_PAGE && (
              <Button size="sm" variant="ghost" icon={ChevronUp} onClick={() => setLimit(FIRST_PAGE)}>
                收起
              </Button>
            )}
            {rest > NEXT_PAGE && (
              <Button size="sm" variant="ghost" onClick={() => setLimit(visible.length)}>
                显示全部
              </Button>
            )}
            {rest > 0 && (
              <Button size="sm" icon={ChevronDown} onClick={() => setLimit((value) => value + NEXT_PAGE)}>
                再显示 {Math.min(rest, NEXT_PAGE)} 个
              </Button>
            )}
          </footer>
        )}
      </section>
    </div>
  );
}

interface ImageProtocolMeta {
  defaultSuffix: string;
  generatePath: string;
  editPath: string;
  auth: string;
  note: string;
}

const IMAGE_PROTOCOL_META: Record<string, ImageProtocolMeta> = {
  openai_images: {
    defaultSuffix: "/v1",
    generatePath: "/images/generations",
    editPath: "/images/edits（multipart）",
    auth: "Authorization: Bearer",
    note: "适用于 OpenAI 官方、CPA 的 GPT Image / Grok Imagine 及标准 OpenAI Images 兼容接口。",
  },
  chat_completions: {
    defaultSuffix: "/v1",
    generatePath: "/chat/completions",
    editPath: "/chat/completions",
    auth: "Authorization: Bearer",
    note: "适用于 CPA Gemini / Antigravity、OpenRouter 及通过 Chat Completions 返回图片的聚合接口。",
  },
  responses: {
    defaultSuffix: "/v1",
    generatePath: "/responses",
    editPath: "/responses",
    auth: "Authorization: Bearer",
    note: "适用于 OpenAI / Azure / CPA Responses API 内置 image_generation 工具。",
  },
  gemini: {
    defaultSuffix: "/v1beta",
    generatePath: "/models/{model}:generateContent",
    editPath: "/models/{model}:generateContent",
    auth: "x-goog-api-key",
    note: "适用于 Google AI Studio 与 CPA /v1beta（如 gemini-3.1-flash-image、gemini-3-pro-image），无二次转译。",
  },
  xai_images: {
    defaultSuffix: "/v1",
    generatePath: "/images/generations",
    editPath: "/images/edits（JSON）",
    auth: "Authorization: Bearer",
    note: "适用于 xAI Grok Imagine 官方直连（api.x.ai）。",
  },
  ark_images: {
    defaultSuffix: "/api/v3",
    generatePath: "/images/generations",
    editPath: "/images/generations",
    auth: "Authorization: Bearer",
    note: "适用于火山方舟 / BytePlus Ark Seedream（文生图与图生图共用同一接口）。",
  },
  dashscope: {
    defaultSuffix: "/api/v1",
    generatePath: "/services/aigc/multimodal-generation/generation",
    editPath: "/services/aigc/multimodal-generation/generation",
    auth: "Authorization: Bearer",
    note: "适用于阿里百炼 DashScope 原生同步多模态生图与改图（qwen-image / wan2.6+）。",
  },
};

function resolveImageBasePreview(baseUrl: string, protocol: string): string {
  const stripped = baseUrl.trim().replace(/\/+$/, "");
  if (!stripped) return "";
  try {
    const parsed = new URL(stripped);
    if (parsed.pathname === "" || parsed.pathname === "/") {
      const suffix = IMAGE_PROTOCOL_META[protocol]?.defaultSuffix ?? "/v1";
      return stripped + suffix;
    }
  } catch {
    return stripped;
  }
  return stripped;
}

interface ImagePreviewResponse {
  mime_type: string;
  size: number;
  protocol: string;
  endpoint: string;
  elapsed_ms: number;
  data_url: string;
  revised_prompt?: string;
}

/** 按已保存的设置测试生图接口；有未保存的图像生成修改时先保存。 */
async function saveImageSettings(): Promise<boolean> {
  return !getConfig().changed.some((path) => path[0] === "image") || (await save());
}

function ImagePreviewCard({ ready }: { ready: boolean }) {
  const [testPrompt, setTestPrompt] = useState("一只戴着草帽的橘猫，简约清新插画风格");
  const [aspectRatio, setAspectRatio] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImagePreviewResponse | null>(null);

  const runPreview = async () => {
    setBusy(true);
    setError("");
    try {
      if (!(await saveImageSettings())) {
        setError("请先修正并保存图像生成设置");
        return;
      }
      const payload: Record<string, string> = { prompt: testPrompt.trim() };
      if (aspectRatio.trim()) payload.aspect_ratio = aspectRatio.trim();
      const response = await post<ImagePreviewResponse>("/api/image/preview", payload);
      setResult(response);
    } catch (failure) {
      setResult(null);
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card form-section" id="section-image-preview">
      <header className="section-header">
        <div className="grow">
          <h2>测试生图</h2>
          <p>
            按已保存的配置发起一次测试文生图（不要求先启用，不发送到 QQ，不写入工作区），用于当场验证接口地址、密钥、模型与协议是否匹配。
          </p>
        </div>
      </header>
      <div className="form-grid">
        <FieldShell
          id="image-preview-prompt"
          label="测试提示词"
          help="最多 500 字，仅用于本次连通性与协议验证。"
        >
          <input
            id="image-preview-prompt"
            className="input"
            value={testPrompt}
            maxLength={500}
            onChange={(event) => setTestPrompt(event.target.value)}
          />
        </FieldShell>
        <FieldShell
          id="image-preview-ratio"
          label="宽高比（可选）"
          help="留空由接口默认；例如 1:1、16:9、3:4。"
        >
          <input
            id="image-preview-ratio"
            className="input mono"
            value={aspectRatio}
            maxLength={32}
            placeholder="留空由接口默认"
            onChange={(event) => setAspectRatio(event.target.value)}
          />
        </FieldShell>
        <FieldShell id="image-preview-action" label="执行测试">
          <div id="image-preview-action" className="stack-sm">
            <div className="row wrap">
              <Button
                variant="primary"
                icon={Play}
                loading={busy}
                disabled={!ready || busy || !testPrompt.trim()}
                onClick={() => void runPreview()}
              >
                {busy ? "正在生图…" : "测试生图"}
              </Button>
              {!ready && <span className="muted">请先填写接口地址和模型名称</span>}
            </div>
            {error && (
              <Banner tone="danger" icon={CircleAlert}>
                {error}
              </Banner>
            )}
            {result && (
              <div className="image-preview-box">
                <div className="row wrap">
                  <Badge tone="success">测试成功</Badge>
                  <Badge tone="neutral">{result.protocol}</Badge>
                  <Badge tone="neutral">{result.mime_type}</Badge>
                  <Badge tone="neutral">{formatBytes(result.size)}</Badge>
                  <Badge tone="neutral">{result.elapsed_ms} ms</Badge>
                </div>
                <img
                  src={result.data_url}
                  alt="测试生图结果预览"
                  className="image-preview-img"
                />
                {result.revised_prompt && (
                  <p className="muted small">润色提示词：{result.revised_prompt}</p>
                )}
              </div>
            )}
          </div>
        </FieldShell>
      </div>
    </section>
  );
}

function ImageGeneration() {
  const state = useConfig();
  const image = object(state.draft.image);
  const enabled = image.enabled === true;
  const protocol =
    typeof image.protocol === "string" && image.protocol in IMAGE_PROTOCOL_META
      ? image.protocol
      : "openai_images";
  const baseUrl = typeof image.base_url === "string" ? image.base_url.trim() : "";
  const model = typeof image.model === "string" ? image.model.trim() : "";
  const hasEndpoint = baseUrl !== "" && model !== "";
  const meta = IMAGE_PROTOCOL_META[protocol];
  const resolvedBase = resolveImageBasePreview(baseUrl, protocol);
  const cleanModel = model.replace(/^models\//, "") || "{model}";
  const generateUrl =
    protocol === "dashscope" && resolvedBase.endsWith(meta.generatePath)
      ? resolvedBase
      : (resolvedBase || "<接口地址>") +
        (protocol === "gemini"
          ? "/models/" + cleanModel + ":generateContent"
          : meta.generatePath);

  return (
    <div className="stack">
      {enabled && !hasEndpoint && (
        <Banner tone="warning" icon={CircleAlert}>
          请填写生图接口地址和模型名称，否则 Agent 无法调用生图工具。
        </Banner>
      )}
      <ConfigSection def={imageConnectionSection} base={["image"]}>
        <FieldShell id="image-endpoint-preview" label="实际请求预览" help={meta.note}>
          <div id="image-endpoint-preview" className="image-endpoint-preview">
            <div className="image-endpoint-line">
              <Badge tone="neutral">POST</Badge>
              <code className="mono">{generateUrl}</code>
            </div>
            <div className="row wrap small muted">
              <span>鉴权：{meta.auth}</span>
              {meta.editPath !== meta.generatePath && <span>· 改图：{meta.editPath}</span>}
            </div>
          </div>
        </FieldShell>
      </ConfigSection>
      <ConfigSection def={imageRequestSection} base={["image"]} />
      <ImagePreviewCard ready={hasEndpoint} />
    </div>
  );
}

export function ToolsPage({ route }: { route: ToolsRoute }) {
  const state = useConfig();
  const runtime = useResource(runtimeResource, 5000);
  const wide = useMediaQuery("(min-width: 768px)");
  const servers = object(state.draft.mcp);
  const names = Object.keys(servers);
  const imageEnabled = object(state.draft.image).enabled === true;
  const shownMcp = route.view === "mcp" ? route.id || (wide ? (names[0] ?? "") : "") : "";
  const onAddMcp = () => void addServer(servers);

  const builtinViews = [
    {
      id: "catalog" as const,
      to: "/tools",
      label: "工具目录",
      sub: "查看全部工具与开放范围",
      icon: Wrench,
      status: null,
    },
    {
      id: "image" as const,
      to: "/tools/image",
      label: "图像生成",
      sub: imageEnabled ? "已启用 · 生图接口与参考图" : "未启用 · 生图接口设置",
      icon: Play,
      status: imageEnabled ? ("success" as const) : ("neutral" as const),
    },
    {
      id: "napcat" as const,
      to: "/tools/napcat",
      label: "NapCat 覆盖",
      sub: "OneBot 动作覆盖清单",
      icon: Check,
      status: null,
    },
  ];
  const currentBuiltin = builtinViews.find((item) => item.id === route.view);

  const mobileTabs = (
    <div className="mobile-subnav">
      <Tabs
        label="工具页面"
        value={route.view}
        onChange={(view) => void navigate(view === "catalog" ? "/tools" : "/tools/" + view)}
        items={[
          { id: "catalog", label: "工具目录" },
          { id: "image", label: "图像生成", count: imageEnabled ? "已启用" : undefined },
          { id: "mcp", label: "MCP 服务", count: names.length || undefined },
          { id: "napcat", label: "NapCat 覆盖" },
        ]}
      />
    </div>
  );

  return (
    <MasterDetail
      label="工具与服务"
      mode={route.view === "mcp" && names.length > 0 ? "split" : "section"}
      hasDetail={route.view !== "mcp" || !!route.id || !names.length}
      list={
        <>
          <div className="md-list-head">
            <div className="md-list-search-row">
              <span className="md-pane-title">工具与服务</span>
              <IconButton icon={Plus} label="添加 MCP 服务" className="md-add-btn" onClick={onAddMcp} />
            </div>
          </div>
          <ul className="md-items">
            <li className="md-section">内置模块</li>
            {builtinViews.map((item) => {
              const active = route.view === item.id;
              return (
                <li key={item.id}>
                  <a
                    href={item.to}
                    className={"md-item" + (active ? " active" : "")}
                    aria-current={active ? "page" : undefined}
                    onClick={linkHandler(item.to)}
                  >
                    <QQAvatar kind="icon" icon={item.icon} name={item.label} size="lg" />
                    <span className="md-item-text">
                      <span className="qq-item-top">
                        <span className="md-item-title">{item.label}</span>
                        {item.status && <StatusDot tone={item.status} />}
                      </span>
                      <span className="qq-item-bottom">
                        <span className="md-item-sub">{item.sub}</span>
                      </span>
                    </span>
                  </a>
                </li>
              );
            })}
            <li className="md-section">MCP 服务 · {names.length}</li>
            {names.map((name) => {
              const enabled = object(servers[name]).enabled === true;
              const status = runtime.data?.mcp[name];
              const label = enabled ? mcpState(status?.state) : mcpState("disabled");
              const to = "/tools/mcp/" + enc(name);
              const active = route.view === "mcp" && shownMcp === name;
              return (
                <li key={name}>
                  <a
                    href={to}
                    className={"md-item" + (active ? " active" : "")}
                    aria-current={active ? "page" : undefined}
                    onClick={linkHandler(to)}
                  >
                    <QQAvatar kind="icon" icon={Plug} name={name} size="lg" />
                    <span className="md-item-text">
                      <span className="qq-item-top">
                        <span className="md-item-title">{name}</span>
                        <StatusDot tone={label.tone} label={label.text} />
                      </span>
                      <span className="qq-item-bottom">
                        <span className="md-item-sub">
                          {label.text}
                          {status?.state === "connected" && status.tools !== undefined
                            ? " · " + status.tools + " 个工具"
                            : ""}
                        </span>
                      </span>
                    </span>
                  </a>
                </li>
              );
            })}
            {!names.length && (
              <li className="md-empty">
                <p>还没有 MCP 服务。</p>
                <Button size="sm" icon={Plus} onClick={onAddMcp}>
                  添加 MCP 服务
                </Button>
              </li>
            )}
          </ul>
        </>
      }
    >
      {route.view === "mcp" ? (
        shownMcp ? (
          <McpDetail key={shownMcp} name={shownMcp} onAdd={onAddMcp} subnav={mobileTabs} />
        ) : (
          <div className="detail">
            <div className="detail-sticky">
              <DetailHeader
                avatar={<QQAvatar kind="icon" icon={Plug} name="MCP" size="md" />}
                title="MCP 服务"
                save
                subtitle="Agent 可以调用的外部 MCP 工具服务；每个会话能用哪些，由会话的能力权限决定"
                actions={
                  <Button icon={Plus} onClick={onAddMcp}>
                    添加 MCP 服务
                  </Button>
                }
              />
              {mobileTabs}
            </div>
            <div className="detail-body">
              <EmptyState
                icon={Plug}
                title="还没有 MCP 服务"
                action={
                  <Button variant="primary" icon={Plus} onClick={onAddMcp}>
                    添加 MCP 服务
                  </Button>
                }
              >
                MCP 服务可以为 Agent 提供额外的工具，例如搜索或文档查询。添加后需要重启 Harness 才会连接。
              </EmptyState>
            </div>
          </div>
        )
      ) : (
        <div className="detail">
          <div className="detail-sticky">
            <DetailHeader
              avatar={
                <QQAvatar
                  kind="icon"
                  icon={currentBuiltin?.icon ?? Wrench}
                  name={currentBuiltin?.label ?? "工具"}
                  size="md"
                />
              }
              title={currentBuiltin?.label ?? "工具"}
              save={route.view === "image"}
              subtitle="Agent 可以调用的工具；每个会话能用哪些，由会话的能力权限决定"
            />
            {mobileTabs}
          </div>
          <div className="detail-body stack">
            {route.view === "catalog" ? (
              <Catalog selected={route.tool} />
            ) : route.view === "image" ? (
              <ImageGeneration />
            ) : (
              <Coverage />
            )}
          </div>
        </div>
      )}
    </MasterDetail>
  );
}
