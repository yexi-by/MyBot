import { useEffect, useMemo, useState } from "react";
import { CircleAlert, ExternalLink, Plug, Plus, Trash2, Wrench } from "lucide-react";
import { Combobox } from "../components/Combobox";
import { ConfigSection } from "../components/form";
import { Menu } from "../components/Menu";
import { Badge, Banner, Button, EmptyState, Segmented, Spinner, StatusDot, Switch, Tabs } from "../components/primitives";
import { DetailHeader, MasterDetail, PageHeader, SearchInput } from "../components/shell";
import { ToolDrawer } from "../components/ToolDrawer";
import { change, useConfig } from "../lib/config";
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
import { defaultMcp, mcpConnectionSection, mcpSecretsSection, mcpToolsSection } from "../schema/fields";

type ToolsRoute = Extract<Route, { page: "tools" }>;
const enc = encodeURIComponent;

type Kind = "all" | "read" | "effects" | "closed";

function Catalog({ selected }: { selected: string }) {
  const state = useConfig();
  const tools = useResource(toolsResource);
  const access = useResource(accessResource);
  const groups = useGroupNames();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [kind, setKind] = useState<Kind>("all");
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
      (kind !== "read" || !tool.effects) &&
      (kind !== "effects" || tool.effects) &&
      (kind !== "closed" || !(openIn.get(tool.name)?.length ?? 0)) &&
      (!text || (tool.title + " " + tool.name + " " + tool.description).toLowerCase().includes(text)),
  );
  const open = (name: string | null) =>
    void navigate(name ? "/tools?tool=" + enc(name) : "/tools", { replace: true, keepScroll: true });
  const current: Tool | null = selected ? (catalog.get(selected) ?? null) : null;
  return (
    <div className="stack">
      <div className="toolbar">
        <SearchInput value={query} onChange={setQuery} placeholder="搜索工具名称或说明" label="搜索工具" />
        <Combobox
          className="toolbar-select"
          label="分类"
          value={category}
          onChange={setCategory}
          options={[
            { value: "", label: "全部分类" },
            ...(tools.data.categories ?? []).map((item) => ({ value: item.id, label: item.label })),
          ]}
        />
        <Segmented
          size="sm"
          label="工具类型"
          value={kind}
          onChange={setKind}
          options={[
            { value: "all", label: "全部" },
            { value: "read", label: "只读" },
            { value: "effects", label: "会执行操作" },
            { value: "closed", label: "未开放" },
          ]}
        />
        <span className="grow" />
        <span className="muted small num">
          {visible.length} / {all.length} 个工具
        </span>
      </div>
      {visible.length ? (
        <div className="card table-wrap">
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
              {visible.map((tool) => {
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
                        {tool.effects && <Badge tone="warning">会执行操作</Badge>}
                      </div>
                    </td>
                    <td className="hide-narrow secondary">{labels.get(tool.category) ?? tool.category}</td>
                    <td className="hide-narrow secondary nowrap">{toolScopes[tool.scope]}</td>
                    <td>
                      {scopes.length ? (
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
      ) : (
        <EmptyState icon={Wrench} title="没有符合条件的工具" compact>
          调整搜索词或筛选条件后再试。
        </EmptyState>
      )}
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
      <ConfigSection
        def={mcpToolsSection}
        base={base}
        suggestions={{ read_only_tools: discovered, effect_tools: discovered }}
        describe={{
          read_only_tools: (item) => discovered.find((tool) => tool.value === item)?.label,
          effect_tools: (item) => discovered.find((tool) => tool.value === item)?.label,
        }}
      />
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
  return (
    <div className="stack">
      <p className="secondary small">
        对照 NapCat {data.version}（{data.commit.slice(0, 7)}）的全部 OneBot 动作，说明每个动作是否作为工具提供。
        <a href={data.source_url} target="_blank" rel="noreferrer" className="inline-link">
          上游源码 <ExternalLink size={13} aria-hidden="true" />
        </a>
      </p>
      {data.aliases && <p className="muted small">{data.aliases}</p>}
      <div className="toolbar">
        <SearchInput value={query} onChange={setQuery} placeholder="搜索动作或工具" label="搜索覆盖清单" />
        <Segmented
          size="sm"
          label="覆盖状态"
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: "全部", count: data.actions.length },
            ...statuses.map((value) => ({
              value,
              label: coverageState(value).text,
              count: data.actions.filter((action) => action.status === value).length,
            })),
          ]}
        />
      </div>
      <div className="card table-wrap">
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
            {visible.map((action) => {
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
        {!visible.length && <p className="table-empty muted">没有符合条件的动作</p>}
      </div>
    </div>
  );
}

export function ToolsPage({ route }: { route: ToolsRoute }) {
  const state = useConfig();
  const servers = object(state.draft.mcp);
  return (
    <div className="page">
      <PageHeader
        title="工具"
        description="Agent 可以调用的工具；每个会话能用哪些，由会话的能力权限决定"
        save={route.view === "mcp"}
        actions={
          route.view === "mcp" && Object.keys(servers).length > 0 ? (
            <Button icon={Plus} onClick={() => void addServer(servers)}>
              添加 MCP 服务
            </Button>
          ) : undefined
        }
      />
      <Tabs
        label="工具页面"
        value={route.view}
        onChange={(view) => void navigate(view === "catalog" ? "/tools" : "/tools/" + view)}
        items={[
          { id: "catalog", label: "工具目录" },
          { id: "mcp", label: "MCP 服务", count: Object.keys(servers).length || undefined },
          { id: "napcat", label: "NapCat 覆盖" },
        ]}
      />
      {route.view === "catalog" ? (
        <Catalog selected={route.tool} />
      ) : route.view === "mcp" ? (
        <McpServers id={route.id} />
      ) : (
        <Coverage />
      )}
    </div>
  );
}
