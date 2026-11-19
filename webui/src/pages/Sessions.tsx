import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowDown,
  Bot,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  CircleStop,
  Play,
  RotateCcw,
  ScrollText,
  Send,
  SlidersHorizontal,
} from "lucide-react";
import { Badge, Banner, Button, EmptyState, Meter, Spinner, StatusDot } from "../components/primitives";
import { CopyButton, DetailHeader, MasterDetail, QQAvatar, SearchInput } from "../components/shell";
import { conversationRoute, scopeLabel } from "../lib/conversations";
import {
  formatBytes,
  formatClock,
  formatDuration,
  formatNumber,
  formatRelative,
  parseScopeKey,
} from "../lib/format";
import { resultStatus, subagentState, turnState, usageSources } from "../lib/labels";
import { linkHandler, navigate, prefersReducedMotion } from "../lib/navigation";
import { runtimeResource, toolsResource, useGroupNames, useResource } from "../lib/resource";
import {
  groupEntries,
  isBusy,
  isSubagentActive,
  messageText,
  pretty,
  resetSession,
  sendPreview,
  sendSubagentInstruction,
  senderLabel,
  sessionRoute,
  stopSubagent,
  stopTurn,
  subagentRoute,
  useSessionDetail,
  useSubagentDetail,
} from "../lib/sessions";
import type { TimelineBlock } from "../lib/sessions";
import { object } from "../lib/types";
import type {
  Json,
  QQMessage,
  RuntimeEnvelope,
  SessionCapacity,
  SessionDetail,
  SessionEntry,
  SubagentDetail,
  SubagentSummary,
  ToolOutcome,
} from "../lib/types";
import type { Tone } from "../lib/ui";
import type { Route } from "../routes";

type SessionsRoute = Extract<Route, { page: "sessions" }>;
type EntryOf<K extends SessionEntry["kind"]> = Extract<SessionEntry, { kind: K }>;

interface Row {
  key: string;
  state: string;
  idle: boolean;
  turns: number;
  updatedAt: number;
  tokens: number;
  cleared: boolean;
  activeSubagents: number;
  /** 首次唤醒正在准备，会话对象还没有建立。 */
  preparing: boolean;
}

function rowsOf(data: RuntimeEnvelope | null): Row[] {
  if (!data) return [];
  const rows: Row[] = data.sessions.map((session) => ({
    key: session.scope_key,
    state: session.state,
    idle: Boolean(session.idle),
    turns: session.turns,
    updatedAt: session.updated_at,
    tokens: session.context.input_tokens ?? 0,
    cleared: session.cleared,
    activeSubagents: session.active_subagents ?? 0,
    preparing: false,
  }));
  const known = new Set(rows.map((row) => row.key));
  for (const turn of data.turns)
    if (!known.has(turn.scope_key))
      rows.push({
        key: turn.scope_key,
        state: turn.state,
        idle: Boolean(turn.idle),
        turns: 0,
        updatedAt: turn.started_at,
        tokens: 0,
        cleared: false,
        activeSubagents: 0,
        preparing: true,
      });
  return rows.sort(
    (a, b) =>
      Number(isBusy(b.state) || b.activeSubagents > 0) -
        Number(isBusy(a.state) || a.activeSubagents > 0) || b.updatedAt - a.updatedAt,
  );
}

const stateTone = (state: string): Tone =>
  state === "running" ? "accent" : state === "compacting" || state === "retrying" ? "warning" : "neutral";

function formatElapsed(seconds: number) {
  if (seconds >= 60) return formatDuration(seconds);
  return (seconds < 10 ? Math.max(0, seconds).toFixed(1) : Math.round(seconds)) + " 秒";
}

function useToolTitles() {
  const tools = useResource(toolsResource).data;
  return useMemo(() => new Map((tools?.tools ?? []).map((tool) => [tool.name, tool.title])), [tools]);
}

function CapacityBar({ capacity }: { capacity: SessionCapacity | undefined }) {
  if (!capacity) return null;
  const ratio = Math.max(capacity.sessions / capacity.max_sessions, capacity.bytes / capacity.max_bytes);
  return (
    <div className="session-capacity">
      <span className="small secondary num">
        已保留 {formatNumber(capacity.sessions)} / {formatNumber(capacity.max_sessions)} 个会话 · 内存{" "}
        {formatBytes(capacity.bytes, "binary")} / {formatBytes(capacity.max_bytes, "binary")}
      </span>
      <Meter label="会话容量" value={ratio} max={1} tone={ratio >= 0.9 ? "warning" : "accent"} />
    </div>
  );
}

function SessionList({
  route,
  rows,
  capacity,
}: {
  route: SessionsRoute;
  rows: Row[];
  capacity?: SessionCapacity;
}) {
  const groups = useGroupNames();
  const [query, setQuery] = useState("");
  const text = query.trim().toLowerCase();
  const visible = rows.filter(
    (row) => !text || (scopeLabel(row.key, groups) + " " + row.key).toLowerCase().includes(text),
  );
  const busy = visible.filter((row) => isBusy(row.state) || row.activeSubagents > 0);
  const idle = visible.filter((row) => !isBusy(row.state) && row.activeSubagents === 0);
  // 只有一个机器人时不重复显示机器人账号。
  const bots = new Set(rows.map((row) => parseScopeKey(row.key)?.bot));
  const item = (row: Row) => {
    const scope = parseScopeKey(row.key);
    const title = scopeLabel(row.key, groups);
    const to = sessionRoute(row.key);
    const active = route.key === row.key;
    const sub = row.preparing
      ? "正在建立会话"
      : row.cleared
        ? "已重置 · 等待唤醒"
        : [
            "第 " + row.turns + " 轮",
            row.updatedAt > 0 ? formatRelative(row.updatedAt) : "",
            row.tokens ? formatNumber(row.tokens) + " token" : "",
          ]
            .filter(Boolean)
            .join(" · ");
    return (
      <li key={row.key}>
        <a
          href={to}
          className={"md-item" + (active ? " active" : "")}
          aria-current={active ? "page" : undefined}
          onClick={linkHandler(to)}
        >
          <QQAvatar
            kind={scope?.kind === "private" ? "user" : "group"}
            id={scope?.peer ?? ""}
            name={title}
            size="lg"
          />
          <span className="md-item-text">
            <span className="qq-item-top">
              <span className="md-item-title">{title}</span>
              {row.updatedAt > 0 && <time className="qq-item-time num">{formatClock(row.updatedAt)}</time>}
            </span>
            <span className="qq-item-bottom">
              <span className="md-item-sub">
                {bots.size > 1 && scope ? "机器人 " + scope.bot + " · " : ""}
                {sub}
              </span>
              <span className="qq-item-badges">
                {row.activeSubagents > 0 && (
                  <span className="qq-pill is-busy num">{row.activeSubagents}子</span>
                )}
                {row.turns > 0 && (
                  <span className={"qq-pill num" + (isBusy(row.state) ? " is-busy" : "")}>{row.turns}轮</span>
                )}
                <StatusDot
                  tone={row.activeSubagents > 0 && !isBusy(row.state) ? "accent" : stateTone(row.state)}
                  label={
                    row.activeSubagents > 0 && !isBusy(row.state)
                      ? `${row.activeSubagents} 个后台子代理运行中`
                      : turnState(row.state, row.idle).text
                  }
                />
              </span>
            </span>
          </span>
        </a>
      </li>
    );
  };
  return (
    <>
      <div className="md-list-head">
        <SearchInput value={query} onChange={setQuery} placeholder="搜索群名或 QQ 号" label="搜索会话" />
        <CapacityBar capacity={capacity} />
      </div>
      <ul className="md-items">
        {busy.length > 0 && <li className="md-section">处理中 · {busy.length}</li>}
        {busy.map(item)}
        {idle.length > 0 && <li className="md-section">等待唤醒 · {idle.length}</li>}
        {idle.map(item)}
        {!rows.length && <li className="md-empty muted">还没有保留的会话。Agent 被唤醒后会出现在这里。</li>}
        {rows.length > 0 && !visible.length && <li className="md-empty muted">没有匹配的会话</li>}
      </ul>
    </>
  );
}

function JsonBlock({ label, value }: { label: string; value: Json | undefined }) {
  return (
    <details className="entry-details">
      <summary>{label}</summary>
      <pre className="entry-pre">{pretty(value)}</pre>
    </details>
  );
}

function MessageLine({ message, botId, tag }: { message: QQMessage; botId: string; tag?: string }) {
  const sender = senderLabel(message, botId);
  return (
    <li className="qq-chat-item is-peer">
      <QQAvatar kind="user" id={message.sender_id} name={sender} size="md" />
      <div className="qq-chat-main">
        <div className="qq-chat-meta">
          {tag && <span className="qq-role-tag">{tag}</span>}
          <span className="session-sender">{sender}</span>
          {message.time > 0 && <time className="tiny muted num">{formatClock(message.time)}</time>}
        </div>
        <div className="qq-bubble bubble-peer">
          <span className="entry-text">{messageText(message.segments) || "（空消息）"}</span>
        </div>
      </div>
    </li>
  );
}

function Entry({
  label,
  note,
  tone = "neutral",
  at,
  children,
}: {
  label: string;
  note?: string;
  tone?: Tone;
  at: number | null;
  children: ReactNode;
}) {
  return (
    <li className={"session-entry entry-" + tone}>
      <div className="entry-head">
        <span className="entry-label">{label}</span>
        {note && <span className="tiny muted">{note}</span>}
        {at !== null && <time className="tiny muted num entry-time">{formatClock(at)}</time>}
      </div>
      <div className="entry-body">{children}</div>
    </li>
  );
}

function Outcome({ result }: { result: ToolOutcome }) {
  return (
    <div className="entry-outcome">
      {result.message && <p className="small secondary">{result.message}</p>}
      {result.error_type && <p className="tiny muted mono">{result.error_type}</p>}
      {result.data !== undefined && result.data !== null && <JsonBlock label="返回数据" value={result.data} />}
      {result.preview && <JsonBlock label={result.truncated ? "截断预览" : "原始结果"} value={result.preview} />}
    </div>
  );
}

function CallEntry({
  entry,
  botId,
  tools,
  scopeKey,
  subagentsById,
  onSubagentChanged,
}: {
  entry: EntryOf<"call">;
  botId: string;
  tools: Map<string, string>;
  scopeKey?: string;
  subagentsById?: Map<string, SubagentSummary>;
  onSubagentChanged?: () => void;
}) {
  const preview = sendPreview(entry.name, entry.arguments);
  const args = object(entry.arguments);
  const resultData = object(entry.result?.data);
  const target =
    typeof args.target_group_id === "string"
      ? "群 " + args.target_group_id
      : typeof args.target_user_id === "string"
        ? "私聊 " + args.target_user_id
        : "";
  const elapsed = entry.result_at !== null && entry.at !== null ? entry.result_at - entry.at : null;
  const status = entry.result ? resultStatus(entry.result.status) : null;
  const linkedSubId = entry.name.startsWith("subagent__")
    ? typeof resultData.subagent_id === "string"
      ? resultData.subagent_id
      : typeof args.subagent_id === "string"
        ? args.subagent_id
        : ""
    : "";
  const linkedSub = linkedSubId ? subagentsById?.get(linkedSubId) : undefined;
  const linkedState = linkedSub ? subagentState(linkedSub.state) : null;
  const linkedActive = linkedSub ? isSubagentActive(linkedSub.state) : false;
  const linkedHref = linkedSubId && scopeKey ? subagentRoute(scopeKey, linkedSubId) : "";
  return (
    <li className="qq-chat-item is-bot">
      <QQAvatar kind="user" id={botId} name="Bot" size="md" />
      <div className="qq-chat-main">
        <div className="qq-chat-meta">
          <span className="entry-label">{preview !== null ? "发送 QQ 消息" : "工具调用"}</span>
          {entry.at !== null && <time className="tiny muted num">{formatClock(entry.at)}</time>}
        </div>
        <div className={"qq-bubble " + (preview !== null ? "bubble-self" : "bubble-tool")}>
          <div className="entry-call-head">
            <span className="session-tool" title={entry.name}>
              {tools.get(entry.name) ?? entry.name}
            </span>
            <span className="tiny muted mono">{entry.name}</span>
            {status ? <Badge tone={status.tone}>{status.text}</Badge> : <Badge tone="accent">执行中</Badge>}
            {elapsed !== null && <span className="tiny muted num">耗时 {formatElapsed(elapsed)}</span>}
          </div>
          {preview !== null && <p className="entry-text entry-quote">{preview || "（空消息）"}</p>}
          {target && <p className="tiny muted">目标：{target}</p>}
          {linkedSubId && linkedHref && (
            <div className="subagent-inline-card">
              <div className="subagent-inline-main">
                <span className="subagent-inline-title">
                  <Bot size={14} aria-hidden="true" />
                  <span>{linkedSub?.title || linkedSubId}</span>
                  {linkedState && <Badge tone={linkedState.tone}>{linkedState.text}</Badge>}
                </span>
                <span className="tiny muted num">
                  {linkedSub ? (
                    <>
                      {linkedSub.step}/{linkedSub.max_steps} 步 · 耗时 {formatDuration(linkedSub.elapsed_seconds)}
                      {linkedActive && linkedSub.current_tool ? ` · 执行 ${linkedSub.current_tool}` : ""}
                    </>
                  ) : (
                    `${linkedSubId}（可能已被回收）`
                  )}
                </span>
              </div>
              <div className="subagent-inline-actions">
                <a className="btn btn-sm btn-default" href={linkedHref} onClick={linkHandler(linkedHref)}>
                  查看会话
                </a>
                {linkedSub && linkedActive && (
                  <Button
                    size="sm"
                    variant="danger-ghost"
                    icon={CircleStop}
                    onClick={async () => {
                      await stopSubagent(linkedSub.subagent_id, linkedSub.title);
                      onSubagentChanged?.();
                    }}
                  >
                    停止
                  </Button>
                )}
              </div>
            </div>
          )}
          <JsonBlock label="参数" value={entry.arguments} />
          {entry.result && <Outcome result={entry.result} />}
        </div>
      </div>
    </li>
  );
}

function BoundaryEntry({ entry }: { entry: EntryOf<"boundary"> }) {
  const receipts = entry.sent_receipts ?? [];
  const status = resultStatus(entry.outcome);
  const ids = receipts
    .map((receipt) => receipt.message_id ?? receipt.res_id ?? receipt.file_id)
    .filter((value) => value !== undefined && value !== null)
    .map(String);
  return (
    <li className="session-entry session-boundary">
      <Badge tone={status.tone}>{status.text}</Badge>
      <span className="small secondary">
        本轮结束 · 发送 {receipts.length} 条
        {entry.send_attempts ? "（尝试 " + entry.send_attempts + " 次）" : ""}
        {ids.length > 0 && " · 消息 " + ids.join("、")}
      </span>
      {entry.at !== null && <time className="tiny muted num entry-time">{formatClock(entry.at)}</time>}
    </li>
  );
}

function EntryView({
  entry,
  botId,
  tools,
  scopeKey,
  subagentsById,
  onSubagentChanged,
}: {
  entry: SessionEntry;
  botId: string;
  tools: Map<string, string>;
  scopeKey?: string;
  subagentsById?: Map<string, SubagentSummary>;
  onSubagentChanged?: () => void;
}) {
  switch (entry.kind) {
    case "wake":
      return (
        <li className="session-wake-group">
          <ul className="session-messages">
            {entry.messages.map((message, idx) => (
              <MessageLine
                key={message.message_id}
                message={message}
                botId={botId}
                tag={idx === 0 ? "唤醒" : undefined}
              />
            ))}
          </ul>
          {entry.notice && <p className="small session-warning-text">{entry.notice}</p>}
        </li>
      );
    case "task":
      return (
        <Entry label="委派任务" note={entry.title || undefined} tone="accent" at={entry.at}>
          <p className="entry-text">{entry.task}</p>
          {entry.context_hint && (
            <details className="entry-details">
              <summary>补充背景</summary>
              <pre className="entry-pre">{entry.context_hint}</pre>
            </details>
          )}
        </Entry>
      );
    case "steer":
      return (
        <Entry label={entry.resume ? "续跑指令" : "运行中纠偏"} tone="accent" at={entry.at}>
          <div className="subagent-steer-list">
            {entry.messages.map((item, index) => (
              <div key={index} className="subagent-steer-item">
                <span className="qq-role-tag">{item.source || "主会话"}</span>
                <p className="entry-text">{item.text}</p>
              </div>
            ))}
          </div>
        </Entry>
      );
    case "history":
      return (
        <MessageLine message={entry.message} botId={botId} tag="历史" />
      );
    case "knowledge":
      return (
        <Entry label="知识库" at={entry.at}>
          <details className="entry-details">
            <summary>
              {entry.files.length ? entry.files.map((file) => file.path).join("、") : "当前没有知识库文件"}
            </summary>
            {entry.files.map((file) => (
              <div key={file.path} className="entry-file">
                <p className="tiny muted mono">{file.path}</p>
                <pre className="entry-pre">{file.text}</pre>
              </div>
            ))}
          </details>
        </Entry>
      );
    case "media":
      return (
        <Entry label="媒体" at={entry.at} note="原始数据不在控制台显示">
          <span className="chips">
            {entry.items.map((item, index) => (
              <span key={index} className="chip">
                {(item.type === "image" ? "图片" : "音频") +
                  (item.mime ? " · " + item.mime : "") +
                  " · " +
                  formatBytes(item.bytes)}
              </span>
            ))}
          </span>
        </Entry>
      );
    case "notice":
      return (
        <Entry label="运行说明" tone="warning" at={entry.at}>
          <ul className="small session-warning-text">
            {entry.messages.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </Entry>
      );
    case "assistant":
      return (
        <li className="qq-chat-item is-bot">
          <QQAvatar kind="user" id={botId} name="Bot" size="md" />
          <div className="qq-chat-main">
            <div className="qq-chat-meta">
              <span className="entry-label">模型正文</span>
              <span className="tiny muted">未发送到 QQ</span>
              {entry.at !== null && <time className="tiny muted num">{formatClock(entry.at)}</time>}
            </div>
            <div className="qq-bubble bubble-thought">
              <p className="entry-text">{entry.text}</p>
            </div>
          </div>
        </li>
      );
    case "call":
      return (
        <CallEntry
          entry={entry}
          botId={botId}
          tools={tools}
          scopeKey={scopeKey}
          subagentsById={subagentsById}
          onSubagentChanged={onSubagentChanged}
        />
      );
    case "result":
      return (
        <Entry label="工具结果" at={entry.at}>
          <Outcome result={entry.result} />
        </Entry>
      );
    case "boundary":
      return <BoundaryEntry entry={entry} />;
    case "other":
      return (
        <Entry label={entry.label || "其他资料"} at={entry.at}>
          <details className="entry-details">
            <summary>展开内容</summary>
            <pre className="entry-pre">{entry.text}</pre>
          </details>
        </Entry>
      );
    default:
      return (
        <Entry label="资料" at={entry.at}>
          <pre className="entry-pre">{entry.text}</pre>
        </Entry>
      );
  }
}

type TurnItem = { type: "history"; messages: QQMessage[] } | { type: "entry"; entry: SessionEntry };

function TurnSection({
  block,
  current,
  botId,
  tools,
  scopeKey,
  subagentsById,
  onSubagentChanged,
}: {
  block: Extract<TimelineBlock, { type: "turn" }>;
  current: boolean;
  botId: string;
  tools: Map<string, string>;
  scopeKey?: string;
  subagentsById?: Map<string, SubagentSummary>;
  onSubagentChanged?: () => void;
}) {
  const boundary = block.entries.find((entry): entry is EntryOf<"boundary"> => entry.kind === "boundary");
  const wake = block.entries.find(
    (entry) => entry.kind === "wake" || entry.kind === "task" || entry.kind === "steer",
  );
  const started = wake?.at ?? block.entries[0]?.at ?? null;
  const status = boundary ? resultStatus(boundary.outcome) : null;
  const items: TurnItem[] = [];
  for (const entry of block.entries) {
    const last = items.at(-1);
    if (entry.kind !== "history") items.push({ type: "entry", entry });
    else if (last?.type === "history") last.messages.push(entry.message);
    else items.push({ type: "history", messages: [entry.message] });
  }
  return (
    <section className="session-turn">
      <header className="session-turn-head">
        <h3>第 {block.turn} 轮</h3>
        {started !== null && (
          <span className="tiny muted num">
            {formatClock(started)}
            {boundary?.at != null ? " – " + formatClock(boundary.at) : ""}
          </span>
        )}
        {current ? (
          <Badge tone="accent">进行中</Badge>
        ) : (
          status && <Badge tone={status.tone}>{status.text}</Badge>
        )}
        {!wake && <span className="tiny muted">部分内容已压缩</span>}
      </header>
      <ol className="session-entries">
        {items.map((item, index) =>
          item.type === "history" ? (
            <li key={index} className="session-entry session-history-fold">
              <details className="entry-details">
                <summary>补入历史消息 {item.messages.length} 条</summary>
                <ul className="session-messages">
                  {item.messages.map((message) => (
                    <MessageLine key={message.message_id} message={message} botId={botId} />
                  ))}
                </ul>
              </details>
            </li>
          ) : (
            <EntryView
              key={index}
              entry={item.entry}
              botId={botId}
              tools={tools}
              scopeKey={scopeKey}
              subagentsById={subagentsById}
              onSubagentChanged={onSubagentChanged}
            />
          ),
        )}
      </ol>
    </section>
  );
}

function StandaloneEntry({
  entry,
  botId,
  tools,
  scopeKey,
  subagentsById,
  onSubagentChanged,
}: {
  entry: SessionEntry;
  botId: string;
  tools: Map<string, string>;
  scopeKey?: string;
  subagentsById?: Map<string, SubagentSummary>;
  onSubagentChanged?: () => void;
}) {
  if (entry.kind === "system")
    return (
      <details className="session-standalone">
        <summary>
          <span className="entry-label">系统提示</span>
          <span className="tiny muted">运行规则与人格，每轮按当前配置更新</span>
        </summary>
        <pre className="entry-pre">{entry.text}</pre>
      </details>
    );
  if (entry.kind === "summary")
    return (
      <section className="session-standalone session-digest">
        <div className="entry-head">
          <span className="entry-label">{entry.origin === "idle" ? "闲时压缩摘要" : "轮内压缩摘要"}</span>
          <span className="tiny muted">
            {entry.origin === "idle"
              ? (entry.turn !== null ? "第 " + entry.turn + " 轮后闲置时生成，" : "闲置时生成，") + "替代了此前的全部对话原文"
              : (entry.turn !== null ? "第 " + entry.turn + " 轮时生成，" : "") + "替代了更早的原文"}
            {entry.model ? " · " + entry.model : ""}
          </span>
          {entry.at !== null && <time className="tiny muted num entry-time">{formatClock(entry.at)}</time>}
        </div>
        <p className="entry-text">{entry.text}</p>
      </section>
    );
  if (entry.kind === "transfer")
    return (
      <details className="session-standalone">
        <summary>
          <span className="entry-label">切换模型前的资料</span>
          <span className="tiny muted">切换服务商或模型时转入的可见历史</span>
        </summary>
        <pre className="entry-pre">{entry.text}</pre>
      </details>
    );
  return (
    <ol className="session-entries">
      <EntryView
        entry={entry}
        botId={botId}
        tools={tools}
        scopeKey={scopeKey}
        subagentsById={subagentsById}
        onSubagentChanged={onSubagentChanged}
      />
    </ol>
  );
}

function blockKey(block: TimelineBlock, index: number) {
  const first = block.type === "turn" ? block.entries[0] : block.entry;
  return (block.type === "turn" ? "turn:" + block.turn : first.kind) + ":" + (first.at ?? index);
}

function Timeline({
  entries,
  marker,
  active,
  cleared = false,
  botId,
  scopeKey,
  subagentsById,
  onSubagentChanged,
  title = "保留的上下文",
  emptyTitle,
  emptyDetail,
}: {
  entries: SessionEntry[];
  marker: string;
  active: boolean;
  cleared?: boolean;
  botId: string;
  scopeKey?: string;
  subagentsById?: Map<string, SubagentSummary>;
  onSubagentChanged?: () => void;
  title?: string;
  emptyTitle?: string;
  emptyDetail?: string;
}) {
  const tools = useToolTitles();
  const hasEntries = entries.length > 0;
  const blocks = useMemo(() => groupEntries(entries), [entries]);
  const end = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(false);
  const seen = useRef("");
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    const scroller = end.current?.closest(".md-detail") as HTMLElement | null;
    const check = () => {
      const winNear = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
      const paneNear = scroller
        ? scroller.clientHeight + scroller.scrollTop >= scroller.scrollHeight - 160
        : true;
      nearBottom.current = scroller && scroller.scrollHeight > scroller.clientHeight + 4 ? paneNear : winNear;
      if (nearBottom.current) setFresh(false);
    };
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
    scroller?.addEventListener("scroll", check, { passive: true });
    return () => {
      window.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
      scroller?.removeEventListener("scroll", check);
    };
  }, [hasEntries]);
  const jump = () => {
    end.current?.scrollIntoView({ behavior: prefersReducedMotion() ? "instant" : "smooth", block: "end" });
    setFresh(false);
  };
  // 有新条目时：已在底部附近就跟随到最新；否则提示，不打断正在阅读的位置。
  useEffect(() => {
    const previous = seen.current;
    seen.current = marker;
    if (!previous || previous === marker) return;
    if (nearBottom.current)
      end.current?.scrollIntoView({ behavior: prefersReducedMotion() ? "instant" : "smooth", block: "end" });
    else setFresh(true);
  }, [marker]);
  if (!hasEntries)
    return (
      <EmptyState
        icon={ScrollText}
        title={emptyTitle ?? (cleared ? "上下文已清空" : "还没有内容")}
        compact
      >
        {emptyDetail ??
          (cleared ? "下次唤醒时会从近期 QQ 历史重新建立上下文。" : "会话建立后，这里会按轮次显示保留的内容。")}
      </EmptyState>
    );
  const lastTurn = blocks.findLast((block) => block.type === "turn");
  return (
    <section className="session-timeline" aria-label={title}>
      <div className="session-timeline-head">
        <h3 className="grow">{title}</h3>
        <Button size="sm" variant="ghost" icon={ArrowDown} onClick={jump}>
          跳到最新
        </Button>
      </div>
      {blocks.map((block, index) =>
        block.type === "turn" ? (
          <TurnSection
            key={blockKey(block, index)}
            block={block}
            botId={botId}
            tools={tools}
            scopeKey={scopeKey}
            subagentsById={subagentsById}
            onSubagentChanged={onSubagentChanged}
            current={
              active &&
              block === lastTurn &&
              !block.entries.some((entry) => entry.kind === "boundary")
            }
          />
        ) : (
          <StandaloneEntry
            key={blockKey(block, index)}
            entry={block.entry}
            botId={botId}
            tools={tools}
            scopeKey={scopeKey}
            subagentsById={subagentsById}
            onSubagentChanged={onSubagentChanged}
          />
        ),
      )}
      <div ref={end} className="session-end" />
      {fresh && (
        <button type="button" className="session-fresh" onClick={jump}>
          <ArrowDown size={14} aria-hidden="true" />
          有新内容
        </button>
      )}
    </section>
  );
}

function SessionSubagents({
  scopeKey,
  subagents,
  onChanged,
}: {
  scopeKey: string;
  subagents: SubagentSummary[];
  onChanged: () => void;
}) {
  if (!subagents.length) return null;
  const activeCount = subagents.filter((item) => isSubagentActive(item.state)).length;
  return (
    <div className="session-block">
      <h3>
        后台子代理
        <span className="count">{subagents.length}</span>
        {activeCount > 0 && <Badge tone="accent">{activeCount} 个运行中</Badge>}
      </h3>
      <ul className="subagent-list">
        {subagents.map((item) => {
          const st = subagentState(item.state);
          const active = isSubagentActive(item.state);
          const href = subagentRoute(scopeKey, item.subagent_id);
          return (
            <li key={item.subagent_id} className="subagent-list-row">
              <QQAvatar kind="icon" icon={Bot} name={item.title} size="md" tone={st.tone} />
              <div className="subagent-list-main">
                <div className="subagent-list-title">
                  <a href={href} onClick={linkHandler(href)}>
                    {item.title}
                  </a>
                  <Badge tone={st.tone}>{st.text}</Badge>
                  <span className="tiny muted num">
                    {item.step}/{item.max_steps} 步
                  </span>
                </div>
                <div className="subagent-list-meta tiny muted num">
                  <span className="mono">{item.subagent_id}</span>
                  {" · 耗时 "}
                  {formatDuration(item.elapsed_seconds)}
                  {active && item.current_tool ? ` · 执行 ${item.current_tool}` : ""}
                  {!active && item.finished_at ? ` · 结束于 ${formatRelative(item.finished_at)}` : ""}
                  {item.context?.input_tokens ? ` · ${formatNumber(item.context.input_tokens)} token` : ""}
                </div>
              </div>
              {active ? (
                <Button
                  size="sm"
                  variant="danger-ghost"
                  icon={CircleStop}
                  onClick={async () => {
                    await stopSubagent(item.subagent_id, item.title);
                    onChanged();
                  }}
                >
                  停止
                </Button>
              ) : (
                <ChevronRight size={16} className="turn-arrow" aria-hidden="true" />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SessionSummary({
  detail,
  botId,
  scopeKey,
  onSubagentChanged,
}: {
  detail: SessionDetail;
  botId: string;
  scopeKey: string;
  onSubagentChanged: () => void;
}) {
  const tools = useToolTitles();
  const context = detail.context;
  const max = context.max_tokens ?? 0;
  const used = context.input_tokens ?? 0;
  const available = detail.available_tools;
  const subagents = detail.subagents ?? [];
  const hitRatio =
    context.prompt_tokens_total && context.prompt_tokens_total > 0 && context.cached_tokens_total
      ? Math.round((context.cached_tokens_total / context.prompt_tokens_total) * 100)
      : context.input_tokens && context.input_tokens > 0 && context.cached_tokens
        ? Math.round((context.cached_tokens / context.input_tokens) * 100)
        : 0;
  return (
    <section className="card session-overview">
      {max > 0 && (
        <div className="session-meter">
          <Meter
            label="上下文用量"
            value={used}
            max={max}
            marker={context.trigger_tokens}
            tone={used / max >= 0.8 ? "warning" : "accent"}
          />
          <span className="tiny muted num">
            上下文 {formatNumber(used)} / {formatNumber(max)} token
            {context.cached_tokens ? ` · 本次命中缓存 ${formatNumber(context.cached_tokens)}` : ""}
            {context.reasoning_tokens ? ` · 推理 ${formatNumber(context.reasoning_tokens)}` : ""}
            {context.usage_source ? " · " + (usageSources[context.usage_source] ?? context.usage_source) : ""}
          </span>
        </div>
      )}
      <dl className="session-stats">
        <div>
          <dt>已接入</dt>
          <dd className="num">{detail.turns} 轮</dd>
        </div>
        <div>
          <dt>本轮模型请求</dt>
          <dd className="num">
            {context.model_request_count ?? 0} 次
            {(context.compaction_request_count ?? 0) + (context.retry_count ?? 0) > 0 && (
              <span className="stat-note">
                压缩 {context.compaction_request_count ?? 0} · 重试 {context.retry_count ?? 0}
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt>上下文减压 / 压缩</dt>
          <dd className="num">
            {context.compactions ?? 0} 次摘要
            {(context.prunes ?? 0) > 0 && (
              <span className="stat-note">预裁剪 {context.prunes} 次</span>
            )}
          </dd>
        </div>
        {hitRatio > 0 && (
          <div>
            <dt>累计缓存命中率</dt>
            <dd className="num">{hitRatio}%</dd>
          </div>
        )}
        <div>
          <dt>内存占用</dt>
          <dd className="num">{formatBytes(detail.retained_bytes, "binary")}</dd>
        </div>
        <div>
          <dt>最近结束</dt>
          <dd>
            {detail.updated_at > 0
              ? formatRelative(detail.updated_at) + " · " + resultStatus(detail.last_outcome).text
              : "—"}
          </dd>
        </div>
      </dl>
      {subagents.length > 0 && (
        <SessionSubagents scopeKey={scopeKey} subagents={subagents} onChanged={onSubagentChanged} />
      )}
      {detail.pending_messages.length > 0 && (
        <div className="session-block">
          <h3>排队中的唤醒消息 · {detail.pending_messages.length}</h3>
          <ul className="session-messages">
            {detail.pending_messages.map((message) => (
              <MessageLine key={message.message_id} message={message} botId={botId} />
            ))}
          </ul>
        </div>
      )}
      {available.length > 0 && <AvailableTools names={available} titles={tools} />}
    </section>
  );
}

/** 可用工具常有上百个，默认只露出前几行，避免摘要卡片被一整面标签撑满。 */
const TOOL_PREVIEW = 18;
function AvailableTools({ names, titles }: { names: string[]; titles: Map<string, string> }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? names : names.slice(0, TOOL_PREVIEW);
  const rest = names.length - shown.length;
  return (
    <div className="session-block">
      <h3>
        当前可用工具 <span className="count">{names.length}</span>
      </h3>
      <span className="chips">
        {shown.map((name) => (
          <span key={name} className="chip" title={name}>
            {titles.get(name) ?? name}
          </span>
        ))}
        {rest > 0 && (
          <button type="button" className="chip chip-more" onClick={() => setExpanded(true)}>
            <ChevronDown size={13} aria-hidden="true" />
            其余 {rest} 个
          </button>
        )}
        {expanded && names.length > TOOL_PREVIEW && (
          <button type="button" className="chip chip-more" onClick={() => setExpanded(false)}>
            <ChevronUp size={13} aria-hidden="true" />
            收起
          </button>
        )}
      </span>
    </div>
  );
}

function SessionView({ scopeKey, runtime }: { scopeKey: string; runtime: RuntimeEnvelope | null }) {
  const groups = useGroupNames();
  const summary = runtime?.sessions.find((item) => item.scope_key === scopeKey);
  const preparing = runtime?.turns.some((turn) => turn.scope_key === scopeKey) ?? false;
  const runtimeSubs = useMemo(
    () => (runtime?.subagents ?? []).filter((item) => item.scope_key === scopeKey),
    [runtime?.subagents, scopeKey],
  );
  const subSignal = runtimeSubs
    .map((item) => `${item.subagent_id}:${item.state}:${item.step}:${item.updated_at}`)
    .join(",");
  const { detail, missing, error, refresh } = useSessionDetail(
    scopeKey,
    [
      summary?.session_id,
      summary?.updated_at,
      summary?.state,
      summary?.pending,
      summary?.active_subagents,
      preparing,
      subSignal,
    ].join("|"),
  );
  const subagentsById = useMemo(() => {
    const map = new Map<string, SubagentSummary>();
    for (const item of runtimeSubs) map.set(item.subagent_id, item);
    for (const item of detail?.subagents ?? []) map.set(item.subagent_id, item);
    return map;
  }, [runtimeSubs, detail?.subagents]);
  const scope = parseScopeKey(scopeKey);
  const back = { to: "/sessions", label: "会话列表" };
  const title = scopeLabel(scopeKey, groups);
  const settings = scope ? conversationRoute(scope.kind, scope.peer) : "/conversations";
  const botId = scope?.bot ?? "";
  const avatar = (
    <QQAvatar
      kind={scope?.kind === "private" ? "user" : "group"}
      id={scope?.peer ?? ""}
      name={title}
      size="md"
    />
  );
  if (!detail)
    return (
      <div className="detail">
        <div className="detail-sticky">
          <DetailHeader back={back} avatar={avatar} title={title} />
        </div>
        <div className="detail-body">
          {missing && preparing ? (
            <EmptyState title="正在建立会话" compact>
              首次唤醒正在读取历史，会话建立后会自动显示。
            </EmptyState>
          ) : missing ? (
            <EmptyState
              icon={CircleAlert}
              title="这个会话没有保留的上下文"
              action={<Button onClick={() => void navigate(settings)}>打开会话设置</Button>}
            >
              它可能还没有被唤醒、已被容量回收，或者 Harness 重启过。
            </EmptyState>
          ) : error ? (
            <EmptyState icon={CircleAlert} title="无法读取会话" action={<Button onClick={refresh}>重试</Button>}>
              {error}
            </EmptyState>
          ) : (
            <div className="page-center">
              <Spinner label="正在读取会话…" />
            </div>
          )}
        </div>
      </div>
    );
  const busy = isBusy(detail.state);
  const activeSubs = detail.active_subagents ?? 0;
  const needStop = busy || activeSubs > 0;
  return (
    <div className="detail">
      <div className="detail-sticky">
        <DetailHeader
          back={back}
          avatar={avatar}
          title={title}
          badges={
            <>
              <Badge tone={stateTone(detail.state)}>{turnState(detail.state, detail.idle).text}</Badge>
              {activeSubs > 0 && <Badge tone="accent">{activeSubs} 个子代理运行中</Badge>}
              {detail.cleared && <Badge>已重置</Badge>}
            </>
          }
          subtitle={
            scope && (
              <span className="row wrap">
                <span className="mono">{(scope.kind === "group" ? "群 " : "私聊 ") + scope.peer}</span>
                <CopyButton text={scope.peer} label={scope.kind === "group" ? "复制群号" : "复制 QQ 号"} />
                <span className="muted">机器人 {scope.bot}</span>
                {detail.model && <span className="muted">模型 {detail.model}</span>}
              </span>
            )
          }
          actions={
            <>
              {busy && (
                <Button
                  icon={CircleStop}
                  onClick={async () => {
                    await stopTurn(scopeKey);
                    refresh();
                  }}
                >
                  停止本轮
                </Button>
              )}
              <Button
                variant="danger-ghost"
                icon={RotateCcw}
                disabled={detail.cleared && !needStop}
                onClick={async () => {
                  await resetSession(scopeKey, busy, activeSubs);
                  refresh();
                }}
              >
                {needStop ? "停止并重置" : "重置上下文"}
              </Button>
              <Button icon={SlidersHorizontal} onClick={() => void navigate(settings)}>
                会话设置
              </Button>
            </>
          }
        />
      </div>
      <div className="detail-body">
        {detail.cleared && (
          <Banner tone="accent" icon={RotateCcw}>
            上下文已重置，下次唤醒时将重新建立。
          </Banner>
        )}
        {error && (
          <p className="danger-text small" role="alert">
            刷新失败：{error}。下面是上次读取的内容。
          </p>
        )}
        <SessionSummary
          detail={detail}
          botId={botId}
          scopeKey={scopeKey}
          onSubagentChanged={refresh}
        />
        <Timeline
          entries={detail.entries ?? []}
          marker={detail.session_id + ":" + detail.version}
          active={busy}
          cleared={detail.cleared}
          botId={botId}
          scopeKey={scopeKey}
          subagentsById={subagentsById}
          onSubagentChanged={refresh}
        />
      </div>
    </div>
  );
}

function SubagentSummaryCard({ detail }: { detail: SubagentDetail }) {
  const context = detail.context;
  const max = context.max_tokens ?? 0;
  const used = context.input_tokens ?? 0;
  const active = isSubagentActive(detail.state);
  const hitRatio =
    context.prompt_tokens_total && context.prompt_tokens_total > 0 && context.cached_tokens_total
      ? Math.round((context.cached_tokens_total / context.prompt_tokens_total) * 100)
      : context.input_tokens && context.input_tokens > 0 && context.cached_tokens
        ? Math.round((context.cached_tokens / context.input_tokens) * 100)
        : 0;
  return (
    <section className="card session-overview">
      {max > 0 && (
        <div className="session-meter">
          <Meter
            label="子代理上下文用量"
            value={used}
            max={max}
            marker={context.trigger_tokens}
            tone={used / max >= 0.8 ? "warning" : "accent"}
          />
          <span className="tiny muted num">
            上下文 {formatNumber(used)} / {formatNumber(max)} token
            {context.cached_tokens ? ` · 本次命中缓存 ${formatNumber(context.cached_tokens)}` : ""}
            {context.reasoning_tokens ? ` · 推理 ${formatNumber(context.reasoning_tokens)}` : ""}
            {context.usage_source ? " · " + (usageSources[context.usage_source] ?? context.usage_source) : ""}
          </span>
        </div>
      )}
      <dl className="session-stats">
        <div>
          <dt>步数进度</dt>
          <dd className="num">
            {detail.step} / {detail.max_steps} 步
            {detail.total_steps > detail.step && (
              <span className="stat-note">累计 {detail.total_steps} 步</span>
            )}
          </dd>
        </div>
        <div>
          <dt>运行时长</dt>
          <dd className="num">
            {formatDuration(detail.elapsed_seconds)}
            {active && detail.idle_seconds >= 5 && (
              <span className="stat-note">静默 {formatDuration(detail.idle_seconds)}</span>
            )}
          </dd>
        </div>
        <div>
          <dt>{active ? "当前执行" : "最近工具"}</dt>
          <dd className="mono">{active ? detail.current_tool || "模型推理中" : detail.last_tool || "—"}</dd>
        </div>
        <div>
          <dt>模型请求</dt>
          <dd className="num">
            {context.model_request_count ?? 0} 次
            {(context.compaction_request_count ?? 0) + (context.retry_count ?? 0) > 0 && (
              <span className="stat-note">
                压缩 {context.compaction_request_count ?? 0} · 重试 {context.retry_count ?? 0}
              </span>
            )}
          </dd>
        </div>
        {hitRatio > 0 && (
          <div>
            <dt>累计缓存命中率</dt>
            <dd className="num">{hitRatio}%</dd>
          </div>
        )}
        <div>
          <dt>内存占用</dt>
          <dd className="num">{formatBytes(detail.retained_bytes, "binary")}</dd>
        </div>
      </dl>
      {detail.generated_ids.length > 0 && (
        <div className="session-block">
          <h3>
            已回传生成媒体 <span className="count">{detail.generated_ids.length}</span>
          </h3>
          <span className="chips">
            {detail.generated_ids.map((gid) => (
              <span key={gid} className="chip mono">
                {gid}
              </span>
            ))}
          </span>
        </div>
      )}
      {detail.queued_steers.length > 0 && (
        <div className="session-block">
          <h3>待注入的纠偏指令 · {detail.queued_steers.length}</h3>
          <div className="subagent-steer-list">
            {detail.queued_steers.map((item, idx) => (
              <div key={idx} className="subagent-steer-item">
                <span className="qq-role-tag">{item.source}</span>
                <p className="entry-text">{item.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function SubagentControlCard({
  detail,
  onSubmitted,
}: {
  detail: SubagentDetail;
  onSubmitted: () => void;
}) {
  const active = isSubagentActive(detail.state);
  const [message, setMessage] = useState("");
  const [extraSteps, setExtraSteps] = useState(20);
  const [submitting, setSubmitting] = useState(false);

  if (!active && !detail.can_resume) return null;

  const submit = async () => {
    const trimmed = message.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    try {
      const result = await sendSubagentInstruction(detail.subagent_id, trimmed, {
        isResume: !active,
        extraSteps: !active ? extraSteps : undefined,
      });
      if (result) {
        setMessage("");
        onSubmitted();
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="card subagent-control">
      <div className="subagent-control-head">
        <h3>{active ? "发送纠偏 / 补充指令" : "在原上下文中续跑子代理"}</h3>
        <span className="tiny muted">
          {active
            ? "将在子代理下一步推理边界注入，用于纠正方向或补充约束（Ctrl+Enter 发送）"
            : "保留子代理已有历史与缓存，按新指令继续调查并自动唤醒主会话（Ctrl+Enter 发送）"}
        </span>
      </div>
      <textarea
        className="subagent-control-input"
        rows={3}
        value={message}
        disabled={submitting}
        placeholder={
          active
            ? "例如：不要再分析无关分支了，直接检查 manga_translator 核心链路并汇报…"
            : "例如：补充核对一下无框文字擦除的掩码阈值逻辑，再给出结论…"
        }
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            void submit();
          }
        }}
      />
      <div className="subagent-control-foot">
        {!active && (
          <label className="subagent-steps-field tiny muted">
            <span>续跑步数上限</span>
            <input
              type="number"
              min={5}
              max={60}
              value={extraSteps}
              disabled={submitting}
              onChange={(event) => {
                const parsed = Number.parseInt(event.target.value, 10);
                if (!Number.isNaN(parsed)) setExtraSteps(Math.min(60, Math.max(5, parsed)));
              }}
            />
          </label>
        )}
        <span className="grow" />
        <Button
          variant="primary"
          size="sm"
          icon={active ? Send : Play}
          loading={submitting}
          disabled={!message.trim()}
          onClick={() => void submit()}
        >
          {active ? "发送纠偏" : "续跑子代理"}
        </Button>
      </div>
    </section>
  );
}

function SubagentView({
  scopeKey,
  subagentId,
  runtime,
}: {
  scopeKey: string;
  subagentId: string;
  runtime: RuntimeEnvelope | null;
}) {
  const groups = useGroupNames();
  const summary = runtime?.subagents?.find((item) => item.subagent_id === subagentId);
  const { detail, missing, error, refresh } = useSubagentDetail(
    subagentId,
    [summary?.state, summary?.step, summary?.updated_at, summary?.pending_steers].join("|"),
  );
  const scope = parseScopeKey(detail?.scope_key ?? scopeKey);
  const effectiveScopeKey = detail?.scope_key ?? scopeKey;
  const parentTitle = scopeLabel(effectiveScopeKey, groups);
  const back = { to: sessionRoute(effectiveScopeKey), label: parentTitle };
  const botId = scope?.bot ?? "";
  const st = subagentState(detail?.state ?? summary?.state);
  const active = isSubagentActive(detail?.state ?? summary?.state);
  const avatar = <QQAvatar kind="icon" icon={Bot} name={detail?.title ?? subagentId} size="md" tone={st.tone} />;

  if (!detail)
    return (
      <div className="detail">
        <div className="detail-sticky">
          <DetailHeader back={back} avatar={avatar} title={summary?.title ?? subagentId} />
        </div>
        <div className="detail-body">
          {missing ? (
            <EmptyState
              icon={CircleAlert}
              title="这个子代理记录不存在或已被回收"
              action={<Button onClick={() => void navigate(sessionRoute(effectiveScopeKey))}>返回主会话</Button>}
            >
              子代理仅保留在有界运行内存中；主会话重置、容量淘汰或 Harness 重启后会清空。
            </EmptyState>
          ) : error ? (
            <EmptyState icon={CircleAlert} title="无法读取子代理详情" action={<Button onClick={refresh}>重试</Button>}>
              {error}
            </EmptyState>
          ) : (
            <div className="page-center">
              <Spinner label="正在读取子代理会话…" />
            </div>
          )}
        </div>
      </div>
    );

  return (
    <div className="detail">
      <div className="detail-sticky">
        <DetailHeader
          back={back}
          avatar={avatar}
          title={detail.title}
          badges={
            <>
              <Badge tone={st.tone}>{st.text}</Badge>
              <Badge>{`${detail.step}/${detail.max_steps} 步`}</Badge>
            </>
          }
          subtitle={
            <span className="row wrap">
              <span className="mono">{detail.subagent_id}</span>
              <CopyButton text={detail.subagent_id} label="复制子代理 ID" />
              <span className="muted">所属 {parentTitle}</span>
              {detail.model && <span className="muted">模型 {detail.model}</span>}
            </span>
          }
          actions={
            <>
              {active && (
                <Button
                  variant="danger-ghost"
                  icon={CircleStop}
                  onClick={async () => {
                    await stopSubagent(detail.subagent_id, detail.title);
                    refresh();
                  }}
                >
                  停止子代理
                </Button>
              )}
              <Button onClick={() => void navigate(sessionRoute(effectiveScopeKey))}>返回主会话</Button>
            </>
          }
        />
      </div>
      <div className="detail-body">
        {detail.error && (
          <Banner tone={detail.state === "failed" ? "danger" : "warning"} icon={CircleAlert}>
            {detail.error}
          </Banner>
        )}
        {error && (
          <p className="danger-text small" role="alert">
            刷新失败：{error}。下面是上次读取的内容。
          </p>
        )}
        <SubagentSummaryCard detail={detail} />
        <SubagentControlCard detail={detail} onSubmitted={refresh} />
        <Timeline
          entries={detail.entries ?? []}
          marker={detail.subagent_id + ":" + detail.version}
          active={active}
          botId={botId}
          title="子代理会话轨迹"
          emptyTitle="还没有执行记录"
          emptyDetail="子代理启动后，任务输入、每步工具调用、纠偏指令与汇报会实时显示在这里。"
        />
      </div>
    </div>
  );
}

export function SessionsPage({ route }: { route: SessionsRoute }) {
  const runtime = useResource(runtimeResource, 3000);
  const rows = useMemo(() => rowsOf(runtime.data), [runtime.data]);
  return (
    <MasterDetail
      label="会话列表"
      hasDetail={!!route.key}
      list={<SessionList route={route} rows={rows} capacity={runtime.data?.capacity} />}
    >
      {route.key && route.subagent ? (
        <SubagentView
          key={route.subagent}
          scopeKey={route.key}
          subagentId={route.subagent}
          runtime={runtime.data}
        />
      ) : route.key ? (
        <SessionView key={route.key} scopeKey={route.key} runtime={runtime.data} />
      ) : (
        <div className="detail">
          <div className="detail-sticky">
            <DetailHeader
              title="会话上下文"
              subtitle="查看各会话当前保留在内存中的对话上下文、工具调用与后台子代理记录"
            />
          </div>
          <div className="detail-body">
            {runtime.error && !runtime.data && (
              <p className="danger-text small" role="alert">
                无法读取运行状态：{runtime.error}
              </p>
            )}
            <EmptyState icon={ScrollText} title="选择一个会话">
              查看它保留的上下文：唤醒消息、模型正文、工具调用与结果、后台子代理与压缩摘要。
            </EmptyState>
          </div>
        </div>
      )}
    </MasterDetail>
  );
}
