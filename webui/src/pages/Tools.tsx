import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  ExternalLink,
  Play,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  WandSparkles,
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
import { DetailHeader, MasterDetail, PageHeader, SearchInput } from "../components/shell";
import { ToolDrawer } from "../components/ToolDrawer";
import { errorMessage, get, post, request } from "../lib/api";
import { change, getConfig, save, useConfig } from "../lib/config";
import { parseServiceAccount } from "../lib/credentials";
import { accessScope } from "../lib/conversations";
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
  ttsAccountSection,
  ttsRequestSection,
  ttsVoiceOptions,
  ttsVoiceSection,
} from "../schema/fields";
import type { Option } from "../schema/fields";

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

function McpDetail({ name }: { name: string }) {
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
      <EmptyState
        icon={CircleAlert}
        title="MCP 服务不存在"
        action={<Button onClick={() => void navigate("/tools/mcp")}>返回列表</Button>}
      >
        “{name}”可能已被删除。
      </EmptyState>
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
          title={name}
          badges={<Badge tone={label.tone}>{label.text}</Badge>}
          subtitle={
            String(server.transport) === "stdio"
              ? String(server.command || "未填写启动命令")
              : String(server.url || "未填写服务地址")
          }
          actions={
            <>
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
      </div>
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
      {!!status?.excluded_tools?.length && <Banner tone="warning" title="部分工具未加载">
        {status.excluded_tools.map((item) => <p key={item.name}>{item.name}：{item.reason}</p>)}
      </Banner>}
    </div>
  );
}

function McpServers({ id }: { id: string }) {
  const state = useConfig();
  const runtime = useResource(runtimeResource, 5000);
  const wide = useMediaQuery("(min-width: 768px)");
  const servers = object(state.draft.mcp);
  const names = Object.keys(servers);
  const shown = id || (wide ? (names[0] ?? "") : "");
  if (!names.length)
    return (
      <EmptyState
        icon={Plug}
        title="还没有 MCP 服务"
        action={
          <Button variant="primary" icon={Plus} onClick={() => void addServer(servers)}>
            添加 MCP 服务
          </Button>
        }
      >
        MCP 服务可以为 Agent 提供额外的工具，例如搜索或文档查询。添加后需要重启 Harness 才会连接。
      </EmptyState>
    );
  return (
    <MasterDetail
      label="MCP 服务"
      hasDetail={!!id}
      list={
        <ul className="md-items">
          {names.map((name) => {
            const enabled = object(servers[name]).enabled === true;
            const status = runtime.data?.mcp[name];
            const label = enabled ? mcpState(status?.state) : mcpState("disabled");
            const to = "/tools/mcp/" + enc(name);
            return (
              <li key={name}>
                <a
                  href={to}
                  className={"md-item" + (shown === name ? " active" : "")}
                  aria-current={shown === name ? "page" : undefined}
                  onClick={linkHandler(to)}
                >
                  <Plug size={16} className="md-item-icon" aria-hidden="true" />
                  <span className="md-item-text">
                    <span className="md-item-title">{name}</span>
                    <span className="md-item-sub">
                      {label.text}
                      {status?.state === "connected" && status.tools !== undefined ? " · " + status.tools + " 个工具" : ""}
                    </span>
                  </span>
                  <StatusDot tone={label.tone} />
                </a>
              </li>
            );
          })}
        </ul>
      }
    >
      {shown ? <McpDetail key={shown} name={shown} /> : null}
    </MasterDetail>
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

function ImageGeneration() {
  const state = useConfig();
  const image = object(state.draft.image);
  const enabled = image.enabled === true;
  const hasEndpoint =
    typeof image.base_url === "string" &&
    image.base_url.trim() !== "" &&
    typeof image.model === "string" &&
    image.model.trim() !== "";
  return (
    <div className="stack">
      {enabled && !hasEndpoint && (
        <Banner tone="warning" icon={CircleAlert}>
          请填写生图接口地址和模型名称，否则 Agent 无法调用生图工具。
        </Banner>
      )}
      <ConfigSection def={imageConnectionSection} base={["image"]} />
      <ConfigSection def={imageRequestSection} base={["image"]} />
    </div>
  );
}

/** 按已保存的设置访问 Vertex；有未保存的语音合成修改时先保存。 */
async function saveSpeechSettings(): Promise<boolean> {
  return !getConfig().changed.some((path) => path[0] === "tts") || (await save());
}

/** 读取失败响应中可展示的中文原因。 */
async function failureDetail(response: Response): Promise<string> {
  const payload: unknown = await response.json().catch(() => ({}));
  const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : null;
  return typeof detail === "string" ? detail : "请求失败（HTTP " + response.status + "）";
}

/** 持有一段录音的对象 URL；换新录音或离开页面时释放占用的内存。 */
function useAudioUrl(): [string | null, (audio: Blob | null) => void] {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(
    () => () => {
      if (url) URL.revokeObjectURL(url);
    },
    [url],
  );
  return [url, (audio) => setUrl(audio ? URL.createObjectURL(audio) : null)];
}

interface CustomVoice {
  id: string;
  name: string;
  kind: string;
}

const voiceKind = (voice: CustomVoice) => (voice.kind === "replicated" ? "复刻音色" : "设计音色");
const voiceOptions = (voices: CustomVoice[]): Option[] =>
  voices.map((voice) => ({ value: voice.id, label: voice.name + " · " + voiceKind(voice) }));

/** 项目中的自定义音色；按已保存的凭据和项目读取，二者变更后重新读取。 */
function useCustomVoices() {
  const vertex = object(object(useConfig().base.tts).vertex);
  const ready = typeof vertex.credentials === "string" && vertex.credentials.trim() !== "";
  const key = JSON.stringify([vertex.credentials, vertex.project_id]);
  const [voices, setVoices] = useState<CustomVoice[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const ticket = useRef(0);
  const reload = useCallback(async () => {
    const current = ++ticket.current;
    if (!ready) {
      setVoices([]);
      setError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const result = await get<{ voices: CustomVoice[] }>("/api/tts/voices");
      if (current !== ticket.current) return;
      setVoices(result.voices);
      setError("");
    } catch (failure) {
      if (current === ticket.current) setError("无法读取自定义音色：" + errorMessage(failure));
    } finally {
      if (current === ticket.current) setLoading(false);
    }
  }, [ready]);
  useEffect(() => {
    setVoices([]);
    setError("");
    void reload();
    return () => {
      ticket.current++;
    };
  }, [reload, key]);
  return { key, ready, voices, loading, error, reload };
}

/** 设计自定义音色并管理项目中保存的音色；音色由管理员选定，Agent 不能更换。 */
function CustomVoices({ list }: { list: ReturnType<typeof useCustomVoices> }) {
  const tts = object(useConfig().draft.tts);
  const current = typeof tts.voice === "string" ? tts.voice : "";
  const location = object(tts.vertex).location;
  const global = location === undefined || location === "global";
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [designed, setDesigned] = useState<CustomVoice | null>(null);
  const [sample, setSample] = useAudioUrl();
  const [listening, setListening] = useState({ id: "", busy: false, error: "" });
  const [audition, setAudition] = useAudioUrl();
  const design = async () => {
    setBusy(true);
    setError("");
    try {
      if (!(await saveSpeechSettings())) {
        setError("请先修正并保存语音合成设置");
        return;
      }
      const result = await post<{ id: string; name: string; sample: string | null }>("/api/tts/voice-design", {
        name: name.trim(),
        description: description.trim(),
      });
      setDesigned({ id: result.id, name: result.name, kind: "prompted" });
      setSample(
        result.sample
          ? new Blob([Uint8Array.from(atob(result.sample), (char) => char.charCodeAt(0))], { type: "audio/wav" })
          : null,
      );
      toast("已设计音色「" + result.name + "」", "success");
      void list.reload();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const select = (voice: CustomVoice) => {
    change(["tts", "voice"], voice.id);
    toast("已将「" + voice.name + "」设为当前音色", "success");
  };
  const listen = async (voice: CustomVoice) => {
    setListening({ id: voice.id, busy: true, error: "" });
    setAudition(null);
    let failure = "";
    try {
      const response = await fetch("/api/tts/voices/" + enc(voice.id) + "/sample");
      if (response.ok) setAudition(await response.blob());
      else failure = await failureDetail(response);
    } catch {
      failure = "无法连接控制台服务，请确认服务正在运行";
    }
    setListening({ id: voice.id, busy: false, error: failure });
  };
  const remove = async (voice: CustomVoice) => {
    const accepted = await confirm({
      title: "删除音色",
      message: "删除「" + voice.name + "」后无法恢复；之后重新设计也不会得到完全相同的声音。",
      confirmLabel: "删除",
      danger: true,
    });
    if (!accepted) return;
    try {
      await request("/api/tts/voices/" + enc(voice.id), { method: "DELETE" });
      toast("已删除音色「" + voice.name + "」", "success");
      if (designed?.id === voice.id) {
        setDesigned(null);
        setSample(null);
      }
      if (listening.id === voice.id) setAudition(null);
      void list.reload();
    } catch (failure) {
      toast(errorMessage(failure), "danger");
    }
  };
  return (
    <section className="card form-section" id="section-tts-voices">
      <header className="section-header">
        <div className="grow">
          <h2>自定义音色</h2>
          <p>用文字描述设计专属音色，保存在 Google Cloud 项目中，一年未使用会自动过期；只能在全球区域使用。</p>
        </div>
        {list.ready && (
          <IconButton
            icon={RefreshCw}
            label="刷新自定义音色"
            disabled={list.loading}
            onClick={() => void list.reload()}
          />
        )}
      </header>
      <div className="form-grid">
        <FieldShell id="tts-design-name" label="音色名称" help="只用于在列表中区分，最多 40 字。">
          <input
            id="tts-design-name"
            className="input"
            value={name}
            maxLength={40}
            placeholder="例如：耄耋本音"
            onChange={(event) => setName(event.target.value)}
          />
        </FieldShell>
        <FieldShell
          id="tts-design-description"
          label="音色描述"
          help="一两句话写清年龄、性别、嗓音质感、口音和基本语气；每句话的情绪由 Agent 在合成时决定。"
        >
          <textarea
            id="tts-design-description"
            className="input textarea"
            rows={3}
            value={description}
            maxLength={500}
            placeholder="例如：二十多岁的年轻女性，声音清亮柔和、略带俏皮，普通话标准，语速适中。"
            onChange={(event) => setDescription(event.target.value)}
          />
        </FieldShell>
        <FieldShell
          id="tts-design-result"
          label="设计结果"
          help={list.ready ? "每次设计都会按语音合成费率计费，并在项目中多保存一个音色。" : "导入服务账号后可以设计音色。"}
        >
          {/* 标签指向容器而不是按钮，按钮名称保持为自身文字。 */}
          <div id="tts-design-result" className="row wrap">
            <Button
              variant="primary"
              icon={WandSparkles}
              loading={busy}
              disabled={!list.ready || busy || !name.trim() || !description.trim()}
              onClick={() => void design()}
            >
              设计音色
            </Button>
            {designed &&
              (designed.id === current ? (
                <Badge tone="success">当前音色</Badge>
              ) : (
                <Button icon={Check} disabled={!global} onClick={() => select(designed)}>
                  设为当前音色
                </Button>
              ))}
          </div>
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          {sample && <audio className="preview-audio" controls src={sample} />}
        </FieldShell>
      </div>
      <div className="voice-list">
        <div className="row">
          <h3>项目中的音色</h3>
          {list.loading && <Spinner label="正在读取" />}
        </div>
        {!global && <p className="field-status">当前区域不是全球，自定义音色不能设为当前音色。</p>}
        {!list.ready ? (
          <p className="field-status">导入服务账号后显示项目中的自定义音色。</p>
        ) : list.error ? (
          <p className="field-status is-error">{list.error}</p>
        ) : list.voices.length === 0 ? (
          !list.loading && <p className="field-status">项目中还没有自定义音色。</p>
        ) : (
          <ul>
            {list.voices.map((voice) => (
              <li key={voice.id}>
                <div className="voice-row">
                  <div className="grow">
                    <strong>{voice.name}</strong>
                    <span className="mono">
                      {voice.id} · {voiceKind(voice)}
                    </span>
                  </div>
                  <div className="row">
                    {voice.kind === "prompted" && (
                      <Button
                        size="sm"
                        icon={Play}
                        loading={listening.busy && listening.id === voice.id}
                        onClick={() => void listen(voice)}
                      >
                        样音
                      </Button>
                    )}
                    {voice.id === current ? (
                      <Badge tone="success">当前音色</Badge>
                    ) : (
                      <Button size="sm" icon={Check} disabled={!global} onClick={() => select(voice)}>
                        设为当前
                      </Button>
                    )}
                    <IconButton
                      icon={Trash2}
                      size="sm"
                      tone="danger"
                      label={"删除音色 " + voice.name}
                      disabled={voice.id === current}
                      onClick={() => void remove(voice)}
                    />
                  </div>
                </div>
                {listening.id === voice.id && listening.error && (
                  <p className="field-error" role="alert">
                    {listening.error}
                  </p>
                )}
                {listening.id === voice.id && audition && (
                  <audio className="preview-audio" controls autoPlay src={audition} />
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** 按已保存的设置合成一小段语音，不经过 QQ；用来确认凭据、模型和音色可用。 */
function SpeechPreview({ ready, voices }: { ready: boolean; voices: CustomVoice[] }) {
  const current = object(useConfig().draft.tts).voice;
  const [text, setText] = useState("你好，这是一段语音合成试听。");
  const [style, setStyle] = useState("");
  const [voice, setVoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [audio, setAudio] = useAudioUrl();
  const hasVoice = voice !== "" || (typeof current === "string" && current !== "");
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      if (!(await saveSpeechSettings())) {
        setError("请先修正并保存语音合成设置");
        return;
      }
      const body = {
        text: text.trim(),
        ...(style.trim() ? { style: style.trim() } : {}),
        ...(voice ? { voice } : {}),
      };
      const response = await fetch("/api/tts/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        setError(await failureDetail(response));
        return;
      }
      setAudio(await response.blob());
    } catch {
      setError("无法连接控制台服务，请确认服务正在运行");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card form-section" id="section-tts-preview">
      <header className="section-header">
        <div className="grow">
          <h2>试听</h2>
          <p>按已保存的设置合成一小段语音，不经过 QQ；每次试听都会产生少量费用。</p>
        </div>
      </header>
      <div className="form-grid">
        <FieldShell id="tts-preview-text" label="试听文本" help="最多 200 字，可插入 <sigh>、<short pause> 等标签。">
          <input
            id="tts-preview-text"
            className="input"
            value={text}
            maxLength={200}
            onChange={(event) => setText(event.target.value)}
          />
        </FieldShell>
        <FieldShell id="tts-preview-style" label="语气" help="可选，例如“开心、轻快”。">
          <input
            id="tts-preview-style"
            className="input"
            value={style}
            maxLength={200}
            placeholder="自然朗读"
            onChange={(event) => setStyle(event.target.value)}
          />
        </FieldShell>
        <FieldShell id="tts-preview-voice" label="试听音色" help="只用于这次试听，不修改当前音色。">
          <Combobox
            id="tts-preview-voice"
            label="试听音色"
            value={voice}
            editable
            options={[{ value: "", label: "使用当前音色" }, ...ttsVoiceOptions, ...voiceOptions(voices)]}
            placeholder="使用当前音色"
            onChange={setVoice}
          />
        </FieldShell>
        <FieldShell
          id="tts-preview-result"
          label="合成结果"
          help={
            !ready ? "导入服务账号并选择模型后可以试听。" : hasVoice ? undefined : "先选择当前音色或试听音色。"
          }
        >
          {/* 标签指向容器而不是按钮，按钮名称保持为自身文字。 */}
          <div id="tts-preview-result" className="row">
            <Button
              variant="primary"
              icon={Play}
              loading={busy}
              disabled={!ready || !hasVoice || busy || !text.trim()}
              onClick={() => void run()}
            >
              合成试听
            </Button>
          </div>
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          {audio && <audio className="preview-audio" controls src={audio} />}
        </FieldShell>
      </div>
    </section>
  );
}

function SpeechSynthesis() {
  const state = useConfig();
  const tts = object(state.draft.tts);
  const credentials = object(tts.vertex).credentials;
  const imported = typeof credentials === "string" && credentials.trim() !== "";
  const hasModel = typeof tts.model === "string" && tts.model !== "";
  const hasVoice = typeof tts.voice === "string" && tts.voice !== "";
  const account = imported ? parseServiceAccount(credentials) : null;
  const project = account && typeof account !== "string" ? account.project : "";
  const voices = useCustomVoices();
  return (
    <div className="stack">
      {tts.enabled === true && (!imported || !hasModel || !hasVoice) && (
        <Banner tone="warning" icon={CircleAlert}>
          请导入服务账号并选择模型和音色，否则 Agent 无法调用语音合成工具。
        </Banner>
      )}
      <ConfigSection def={ttsVoiceSection} base={["tts"]} suggestions={{ voice: voiceOptions(voices.voices) }} />
      <CustomVoices key={"custom-" + voices.key} list={voices} />
      <ConfigSection
        def={ttsAccountSection}
        base={["tts", "vertex"]}
        helpFor={
          project ? { project_id: "调用和计费所在的项目；留空时使用服务账号所属的 " + project + "。" } : undefined
        }
      />
      <ConfigSection def={ttsRequestSection} base={["tts"]} />
      <SpeechPreview key={"preview-" + voices.key} ready={imported && hasModel} voices={voices.voices} />
    </div>
  );
}

export function ToolsPage({ route }: { route: ToolsRoute }) {
  const state = useConfig();
  const servers = object(state.draft.mcp);
  const imageEnabled = object(state.draft.image).enabled === true;
  const ttsEnabled = object(state.draft.tts).enabled === true;
  return (
    <div className="page">
      <PageHeader
        title="工具"
        description="Agent 可以调用的工具；每个会话能用哪些，由会话的能力权限决定"
        save={route.view === "mcp" || route.view === "image" || route.view === "tts"}
        actions={
          route.view === "mcp" && Object.keys(servers).length > 0 ? (
            <Button icon={Plus} onClick={() => void addServer(servers)}>
              添加 MCP 服务
            </Button>
          ) : undefined
        }
      />
      <div className="sticky-tabs">
        <Tabs
          label="工具页面"
          value={route.view}
          onChange={(view) => void navigate(view === "catalog" ? "/tools" : "/tools/" + view)}
          items={[
            { id: "catalog", label: "工具目录" },
            { id: "image", label: "图像生成", count: imageEnabled ? "已启用" : undefined },
            { id: "tts", label: "语音合成", count: ttsEnabled ? "已启用" : undefined },
            { id: "mcp", label: "MCP 服务", count: Object.keys(servers).length || undefined },
            { id: "napcat", label: "NapCat 覆盖" },
          ]}
        />
      </div>
      {route.view === "catalog" ? (
        <Catalog selected={route.tool} />
      ) : route.view === "image" ? (
        <ImageGeneration />
      ) : route.view === "tts" ? (
        <SpeechSynthesis />
      ) : route.view === "mcp" ? (
        <McpServers id={route.id} />
      ) : (
        <Coverage />
      )}
    </div>
  );
}
