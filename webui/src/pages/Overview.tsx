import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity as ActivityIcon,
  Bell,
  Box,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  CircleStop,
  Gauge,
  MessageSquare,
  MessagesSquare,
  Plug,
  Radio,
  Recycle,
  RotateCcw,
  Settings2,
  Shrink,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Button, EmptyState, Meter, Segmented, StatusDot } from "../components/primitives";
import { PageHeader, SearchInput } from "../components/shell";
import { useConfig } from "../lib/config";
import { scopeLabel } from "../lib/conversations";
import { conversationRefs, hasSessionModel } from "../lib/models";
import {
  dayKey,
  formatBytes,
  formatClock,
  formatDay,
  formatDuration,
  formatNumber,
  formatRelative,
  parseScopeKey,
} from "../lib/format";
import { activityKind, mcpState, resultStatus, turnState, usageSources } from "../lib/labels";
import { linkHandler } from "../lib/navigation";
import {
  runtimeResource,
  sandboxResource,
  toolsResource,
  useGroupNames,
  useResource,
} from "../lib/resource";
import { sessionRoute, stopTurn } from "../lib/sessions";
import { getAt, object } from "../lib/types";
import type { Activity, RetainedSession, Turn, TurnContext } from "../lib/types";
import type { Tone } from "../lib/ui";

function Tile({
  icon: Icon,
  label,
  value,
  detail,
  tone,
  to,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  detail: ReactNode;
  tone: Tone;
  to: string;
}) {
  return (
    <a className={"tile tile-" + tone} href={to} onClick={linkHandler(to)}>
      <span className="tile-head">
        <Icon size={16} aria-hidden="true" />
        <span>{label}</span>
        <StatusDot tone={tone} />
      </span>
      <span className="tile-value">{value}</span>
      <span className="tile-detail">{detail}</span>
    </a>
  );
}

/** NapCat 反向 WebSocket 地址。生产环境由后端提供页面，与当前地址同源；Vite 开发时改用后端端口。 */
export function napcatAddress(port?: unknown) {
  const host =
    import.meta.env.DEV && typeof port === "number"
      ? window.location.hostname + ":" + port
      : window.location.host;
  return (window.location.protocol === "https:" ? "wss://" : "ws://") + host + "/napcat/ws";
}

function StatusTiles() {
  const { draft } = useConfig();
  const runtime = useResource(runtimeResource, 3000).data;
  const sandboxEnabled = object(draft.sandbox).enabled === true;
  const sandbox = useResource(sandboxResource, sandboxEnabled ? 15_000 : 0).data;
  const napcat = object(draft.napcat);
  const bots = runtime?.napcat.connected ?? [];
  const names = useGroupNames();
  const providers = Object.keys(object(draft.providers));
  // 只看已启用的会话：启用了却没选好模型的会话不会回复。
  const enabledRefs = conversationRefs(draft, names).filter(
    (ref) => object(getAt(draft, ref.base)).enabled === true,
  );
  const missing = enabledRefs.filter((ref) => !hasSessionModel(draft, object(getAt(draft, ref.base))));
  const models = [
    ...new Set(enabledRefs.map((ref) => String(getAt(draft, [...ref.base, "model", "name"]) ?? ""))),
  ];
  const groups = Object.values(object(draft.groups)).map(object);
  const enabledGroups = groups.filter((group) => group.enabled === true).length;
  const privateEnabled = object(draft.private).enabled === true;
  const servers = Object.entries(object(draft.mcp));
  const enabledServers = servers.filter(([, value]) => object(value).enabled === true);
  const connected = enabledServers.filter(([name]) => runtime?.mcp[name]?.state === "connected").length;

  const qq: [string, ReactNode, Tone] = bots.length
    ? ["已连接", "机器人 " + bots.join("、"), "success"]
    : !napcat.token
      ? ["未设置连接密钥", "NapCat 无法接入，请先在设置中填写密钥", "danger"]
      : ["等待连接", <span className="mono">{napcatAddress(object(draft.server).port)}</span>, "warning"];
  const model: [string, ReactNode, Tone, string] = !providers.length
    ? ["未添加服务商", "添加服务商后，在会话中选择模型", "danger", "/providers"]
    : !enabledRefs.length
      ? ["—", "还没有启用的会话", "neutral", "/conversations"]
      : missing.length
        ? [
            missing.length + " 个会话未选模型",
            missing.map((ref) => ref.label).join("、") + " 不会回复",
            "danger",
            missing[0].route,
          ]
        : [
            models.length === 1 ? models[0] : models.length + " 个模型",
            "已启用的会话都已选好模型",
            "success",
            "/conversations",
          ];
  const sessions: [string, ReactNode, Tone] = [
    enabledGroups + " / " + groups.length + " 个群",
    "私聊" + (privateEnabled ? "已启用" : "未启用"),
    enabledGroups || privateEnabled ? "success" : "warning",
  ];
  const box: [string, ReactNode, Tone] = !sandboxEnabled
    ? ["未启用", "代码执行和共享工作区不可用", "neutral"]
    : !sandbox
      ? ["读取中", "正在检查沙箱状态", "neutral"]
      : !sandbox.available
        ? ["不可用", sandbox.reason || "沙箱控制器没有响应", "danger"]
        : [
            sandbox.busy ? "执行中" : "可用",
            "已用 " + formatBytes(sandbox.used_bytes) + " / " + formatBytes(sandbox.quota_bytes),
            sandbox.busy ? "accent" : "success",
          ];
  const mcp: [string, ReactNode, Tone] = !servers.length
    ? ["未配置", "可在工具页添加 MCP 服务", "neutral"]
    : !enabledServers.length
      ? ["未启用", servers.length + " 个服务均未启用", "neutral"]
      : [
          connected + " / " + enabledServers.length + " 已连接",
          enabledServers
            .map(([name]) => name + " " + mcpState(runtime?.mcp[name]?.state).text)
            .join("，"),
          connected === enabledServers.length ? "success" : "warning",
        ];
  return (
    <div className="tiles">
      <Tile icon={Radio} label="QQ 连接" value={qq[0]} detail={qq[1]} tone={qq[2]} to="/settings/napcat" />
      <Tile
        icon={Sparkles}
        label="会话模型"
        value={model[0]}
        detail={model[1]}
        tone={model[2]}
        to={model[3]}
      />
      <Tile
        icon={MessagesSquare}
        label="已启用会话"
        value={sessions[0]}
        detail={sessions[1]}
        tone={sessions[2]}
        to="/conversations"
      />
      <Tile icon={Box} label="共享沙箱" value={box[0]} detail={box[1]} tone={box[2]} to="/sandbox" />
      <Tile icon={Plug} label="MCP 服务" value={mcp[0]} detail={mcp[1]} tone={mcp[2]} to="/tools/mcp" />
    </div>
  );
}

/** 上下文用量：进度条 + 数字，正在运行和已保留的会话共用同一种呈现。 */
function ContextMeter({ context, showSource = false }: { context: TurnContext; showSource?: boolean }) {
  const max = context.max_tokens ?? 0;
  const used = context.input_tokens ?? 0;
  if (max <= 0) return null;
  return (
    <div className="turn-context">
      <Meter
        label="上下文用量"
        value={used}
        max={max}
        marker={context.trigger_tokens}
        tone={used / max >= 0.8 ? "warning" : "accent"}
      />
      <span className="tiny muted num">
        上下文 {formatNumber(used)} / {formatNumber(max)} token
        {showSource && context.usage_source
          ? " · " + (usageSources[context.usage_source] ?? context.usage_source)
          : ""}
      </span>
    </div>
  );
}

function TurnRow({ turn }: { turn: Turn }) {
  const groups = useGroupNames();
  const state = turnState(turn.state);
  const context = turn.context;
  const route = sessionRoute(turn.scope_key);
  return (
    <li className="turn is-link">
      <div className="turn-main">
        <div className="turn-title">
          <a href={route} onClick={linkHandler(route)}>
            {scopeLabel(turn.scope_key, groups)}
          </a>
          <Badge tone={state.tone}>{turn.idle ? "闲时压缩" : state.text}</Badge>
        </div>
        <div className="turn-meta muted">
          开始于 {formatRelative(turn.started_at)}
          {turn.rounds !== undefined && " · 模型请求 " + turn.rounds + " 次"}
          {context?.compactions ? " · 已压缩 " + context.compactions + " 次" : ""}
        </div>
      </div>
      {context ? <ContextMeter context={context} showSource /> : <span />}
      <Button size="sm" icon={CircleStop} onClick={() => void stopTurn(turn.scope_key)}>
        停止
      </Button>
    </li>
  );
}

function RetainedSessions({ sessions }: { sessions: RetainedSession[] }) {
  const groups = useGroupNames();
  return (
    <section className="card">
      <header className="card-header">
        <div className="grow">
          <h2>已保留会话</h2>
          <p className="card-subtitle">
            再次唤醒会继续使用当前上下文；达到保留上限时会回收最久未活动的会话。
          </p>
        </div>
        <a className="small" href="/sessions" onClick={linkHandler("/sessions")}>
          查看上下文
        </a>
      </header>
      {sessions.length ? (
        <ul className="turns">
          {[...sessions].reverse().map((session) => {
            const state = turnState(session.state);
            const route = sessionRoute(session.scope_key);
            return (
              <li className="turn is-link" key={session.session_id}>
                <div className="turn-main">
                  <div className="turn-title">
                    <a href={route} onClick={linkHandler(route)}>
                      {scopeLabel(session.scope_key, groups)}
                    </a>
                    <Badge tone={state.tone}>{state.text}</Badge>
                    {session.cleared && <Badge>已重置</Badge>}
                  </div>
                  <div className="turn-meta muted">
                    {session.cleared ? "上下文已重置，下次唤醒时重新建立" : "已接入 " + session.turns + " 轮"}
                    {session.updated_at > 0 ? " · 最近结束于 " + formatRelative(session.updated_at) : ""}
                    {session.context.compactions ? " · 已压缩 " + session.context.compactions + " 次" : ""}
                  </div>
                </div>
                {!session.cleared ? <ContextMeter context={session.context} /> : <span />}
                <ChevronRight size={16} className="turn-arrow" aria-hidden="true" />
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="card-empty">暂无已保留的会话。</p>
      )}
    </section>
  );
}

type Filter = "all" | "turn" | "tool" | "problem";
const isProblem = (item: Activity) =>
  item.kind === "error" ||
  item.kind === "overload" ||
  ["error", "unknown", "limit", "timeout"].includes(item.status ?? "");

/** 首屏只放最近的若干条，避免概览页被几百条活动拉得很长；需要时再逐页展开。 */
const FIRST_PAGE = 20;
const NEXT_PAGE = 50;
const kindIcons: Record<string, LucideIcon> = {
  turn: MessageSquare,
  tool: Wrench,
  model: Sparkles,
  compaction: Shrink,
  session: Recycle,
  reset: RotateCcw,
  config: Settings2,
  error: CircleAlert,
  overload: Gauge,
  wake: Bell,
};
/** 成功是常态，不再逐行打绿色徽标；只把需要注意的结果标出来。 */
const quietStatuses = new Set(["ok", "completed"]);

function ActivityFeed({ activity }: { activity: Activity[] }) {
  const groups = useGroupNames();
  const tools = useResource(toolsResource).data;
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(FIRST_PAGE);
  const titles = useMemo(
    () => new Map((tools?.tools ?? []).map((tool) => [tool.name, tool.title])),
    [tools],
  );
  const ordered = useMemo(() => [...activity].reverse(), [activity]);
  // “进行中”只对同一会话、同一类型的最新一条成立；之后已有新事件的开始记录不再显示为进行中。
  const settled = useMemo(() => {
    const seen = new Set<string>();
    const result = new Set<string>();
    for (const item of ordered) {
      const key = item.scope_key + "\u0000" + item.kind;
      if (item.status === "running" && seen.has(key)) result.add(item.id);
      seen.add(key);
    }
    return result;
  }, [ordered]);
  const counts = {
    all: ordered.length,
    turn: ordered.filter((item) => item.kind === "turn" || item.kind === "compaction").length,
    tool: ordered.filter((item) => item.kind === "tool").length,
    problem: ordered.filter(isProblem).length,
  };
  const text = query.trim().toLowerCase();
  const visible = ordered.filter((item) => {
    if (filter === "turn" && item.kind !== "turn" && item.kind !== "compaction") return false;
    if (filter === "tool" && item.kind !== "tool") return false;
    if (filter === "problem" && !isProblem(item)) return false;
    if (!text) return true;
    const tool = item.tool ? (titles.get(item.tool) ?? item.tool) + " " + item.tool : "";
    return (item.message + " " + tool + " " + scopeLabel(item.scope_key, groups)).toLowerCase().includes(text);
  });
  const shown = visible.slice(0, limit);
  const rest = visible.length - shown.length;
  const rows: ReactNode[] = [];
  let lastDay = "";
  for (const item of shown) {
    const day = dayKey(item.time);
    if (day !== lastDay) {
      lastDay = day;
      rows.push(
        <li key={"day:" + day} className="activity-day">
          {formatDay(item.time)}
        </li>,
      );
    }
    const kind = activityKind(item.kind);
    const KindIcon = kindIcons[item.kind] ?? ActivityIcon;
    const status =
      item.status && !quietStatuses.has(item.status) && !settled.has(item.id) ? resultStatus(item.status) : null;
    const route = parseScopeKey(item.scope_key) ? sessionRoute(item.scope_key) : "";
    const problem = isProblem(item)
      ? " is-problem tone-" + (kind.tone === "danger" || status?.tone === "danger" ? "danger" : "warning")
      : "";
    rows.push(
      <li key={item.id} className={"activity-row" + problem}>
        <time className="activity-time num muted" title={new Date(item.time * 1000).toLocaleString("zh-CN")}>
          {formatClock(item.time)}
        </time>
        <span className={"activity-kind tone-" + kind.tone}>
          <KindIcon size={14} aria-hidden="true" />
          {kind.text}
        </span>
        <div className="activity-line">
          {item.tool && (
            <span className="activity-tool" title={item.tool}>
              {titles.get(item.tool) ?? item.tool}
            </span>
          )}
          <span className="activity-message">{item.message}</span>
        </div>
        {route ? (
          <a className="activity-scope" href={route} onClick={linkHandler(route)}>
            {scopeLabel(item.scope_key, groups)}
          </a>
        ) : (
          <span className="activity-scope">{item.scope_key}</span>
        )}
        <span className="activity-status">{status && <Badge tone={status.tone}>{status.text}</Badge>}</span>
      </li>,
    );
  }
  return (
    <section className="card">
      <header className="card-header sticky wrap">
        <h2 className="grow">最近活动</h2>
        <Segmented
          size="sm"
          label="活动类型"
          value={filter}
          onChange={(next) => {
            setFilter(next);
            setLimit(FIRST_PAGE);
          }}
          options={[
            { value: "all", label: "全部", count: counts.all },
            { value: "turn", label: "对话", count: counts.turn },
            { value: "tool", label: "工具", count: counts.tool },
            { value: "problem", label: "异常", count: counts.problem },
          ]}
        />
        <SearchInput
          value={query}
          onChange={(next) => {
            setQuery(next);
            setLimit(FIRST_PAGE);
          }}
          placeholder="搜索活动"
          label="搜索最近活动"
        />
      </header>
      {visible.length ? (
        <>
          <ol className="activity">{rows}</ol>
          {(rest > 0 || limit > FIRST_PAGE) && (
            <footer className="card-footer">
              <span className="muted small num">
                已显示 {shown.length} / {visible.length} 条
              </span>
              <span className="grow" />
              {limit > FIRST_PAGE && (
                <Button size="sm" variant="ghost" icon={ChevronUp} onClick={() => setLimit(FIRST_PAGE)}>
                  收起
                </Button>
              )}
              {rest > 0 && (
                <Button size="sm" icon={ChevronDown} onClick={() => setLimit((value) => value + NEXT_PAGE)}>
                  再显示 {Math.min(rest, NEXT_PAGE)} 条
                </Button>
              )}
            </footer>
          )}
        </>
      ) : (
        <EmptyState icon={ActivityIcon} title={ordered.length ? "没有符合条件的活动" : "还没有活动"} compact>
          {ordered.length ? "调整筛选条件或搜索词后再试。" : "对话唤醒、工具调用与异常事件会显示在这里。"}
        </EmptyState>
      )}
    </section>
  );
}

export function OverviewPage() {
  const runtime = useResource(runtimeResource, 3000);
  const data = runtime.data;
  return (
    <div className="page">
      <PageHeader
        title="概览"
        save={false}
        description={data ? "Harness 已运行 " + formatDuration(Date.now() / 1000 - data.started_at) : "运行状态与最近活动"}
      />
      <StatusTiles />
      {!!data && (data.capacity.rejected_messages > 0 || data.napcat.dropped_events > 0) && (
        <p className="warning-text small" role="status">
          本次启动以来，接入队列已丢弃 {data.napcat.dropped_events} 条事件，运行队列已拒收 {data.capacity.rejected_messages} 条唤醒。
          可在设置中调整队列容量，并查看最近活动。
        </p>
      )}
      {runtime.error && !data && (
        <p className="danger-text small" role="alert">
          无法读取运行状态：{runtime.error}
        </p>
      )}
      <section className="card">
        <header className="card-header">
          <h2 className="grow">正在运行</h2>
          <span className="muted small">{data?.turns.length ? data.turns.length + " 个会话" : ""}</span>
        </header>
        {data?.turns.length ? (
          <ul className="turns">
            {data.turns.map((turn) => (
              <TurnRow key={turn.scope_key} turn={turn} />
            ))}
          </ul>
        ) : (
          <p className="card-empty">当前没有进行中的对话。被唤醒的会话会在这里显示进度和上下文用量。</p>
        )}
      </section>
      <RetainedSessions sessions={data?.sessions ?? []} />
      <ActivityFeed activity={data?.activity ?? []} />
    </div>
  );
}
