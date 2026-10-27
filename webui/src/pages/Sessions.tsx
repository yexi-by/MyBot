import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowDown,
  CircleAlert,
  CircleStop,
  Hash,
  RotateCcw,
  ScrollText,
  SlidersHorizontal,
  TriangleAlert,
  User,
} from "lucide-react";
import { Badge, Banner, Button, EmptyState, Meter, Spinner, StatusDot } from "../components/primitives";
import { CopyButton, DetailHeader, MasterDetail, PageHeader, SearchInput } from "../components/shell";
import { conversationRoute, scopeLabel } from "../lib/conversations";
import {
  formatBytes,
  formatClock,
  formatDuration,
  formatNumber,
  formatRelative,
  parseScopeKey,
} from "../lib/format";
import { resultStatus, turnState, usageSources } from "../lib/labels";
import { linkHandler, navigate, prefersReducedMotion } from "../lib/navigation";
import { runtimeResource, toolsResource, useGroupNames, useResource } from "../lib/resource";
import {
  groupEntries,
  isBusy,
  messageText,
  pretty,
  resetSession,
  resolveOperation,
  sendPreview,
  senderLabel,
  sessionRoute,
  stopTurn,
  useSessionDetail,
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
  ToolOutcome,
} from "../lib/types";
import type { Tone } from "../lib/ui";
import type { Route } from "../routes";

type SessionsRoute = Extract<Route, { page: "sessions" }>;
type EntryOf<K extends SessionEntry["kind"]> = Extract<SessionEntry, { kind: K }>;

interface Row {
  key: string;
  state: string;
  turns: number;
  updatedAt: number;
  tokens: number;
  cleared: boolean;
  /** 首次唤醒正在准备，会话对象还没有建立。 */
  preparing: boolean;
}

function rowsOf(data: RuntimeEnvelope | null): Row[] {
  if (!data) return [];
  const rows: Row[] = data.sessions.map((session) => ({
    key: session.scope_key,
    state: session.state,
    turns: session.turns,
    updatedAt: session.updated_at,
    tokens: session.context.input_tokens ?? 0,
    cleared: session.cleared,
    preparing: false,
  }));
  const known = new Set(rows.map((row) => row.key));
  for (const turn of data.turns)
    if (!known.has(turn.scope_key))
      rows.push({
        key: turn.scope_key,
        state: turn.state,
        turns: 0,
        updatedAt: turn.started_at,
        tokens: 0,
        cleared: false,
        preparing: true,
      });
  return rows.sort(
    (a, b) => Number(isBusy(b.state)) - Number(isBusy(a.state)) || b.updatedAt - a.updatedAt,
  );
}

const stateTone = (state: string): Tone =>
  state === "running" ? "accent" : state === "compacting" ? "warning" : "neutral";

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

function SessionList({ route, rows }: { route: SessionsRoute; rows: Row[] }) {
  const groups = useGroupNames();
  const [query, setQuery] = useState("");
  const text = query.trim().toLowerCase();
  const visible = rows.filter(
    (row) => !text || (scopeLabel(row.key, groups) + " " + row.key).toLowerCase().includes(text),
  );
  const busy = visible.filter((row) => isBusy(row.state));
  const idle = visible.filter((row) => !isBusy(row.state));
  // 只有一个机器人时不重复显示机器人账号。
  const bots = new Set(rows.map((row) => parseScopeKey(row.key)?.bot));
  const item = (row: Row) => {
    const scope = parseScopeKey(row.key);
    const Icon = scope?.kind === "private" ? User : Hash;
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
          <Icon size={16} className="md-item-icon" aria-hidden="true" />
          <span className="md-item-text">
            <span className="md-item-title">{scopeLabel(row.key, groups)}</span>
            <span className="md-item-sub">
              {bots.size > 1 && scope ? "机器人 " + scope.bot + " · " : ""}
              {sub}
            </span>
          </span>
          <StatusDot tone={stateTone(row.state)} label={turnState(row.state).text} />
        </a>
      </li>
    );
  };
  return (
    <>
      <div className="md-list-head">
        <SearchInput value={query} onChange={setQuery} placeholder="搜索群名或 QQ 号" label="搜索会话" />
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

function MessageLine({ message, botId }: { message: QQMessage; botId: string }) {
  return (
    <li className="session-message">
      <span className="session-sender">{senderLabel(message, botId)}</span>
      {message.time > 0 && <time className="tiny muted num">{formatClock(message.time)}</time>}
      <span className="entry-text">{messageText(message.segments) || "（空消息）"}</span>
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

function CallEntry({ entry, tools }: { entry: EntryOf<"call">; tools: Map<string, string> }) {
  const preview = sendPreview(entry.name, entry.arguments);
  const args = object(entry.arguments);
  const target =
    typeof args.target_group_id === "string"
      ? "群 " + args.target_group_id
      : typeof args.target_user_id === "string"
        ? "私聊 " + args.target_user_id
        : "";
  const elapsed = entry.result_at !== null && entry.at !== null ? entry.result_at - entry.at : null;
  const status = entry.result ? resultStatus(entry.result.status) : null;
  return (
    <Entry label="工具调用" at={entry.at}>
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
      <JsonBlock label="参数" value={entry.arguments} />
      {entry.result && <Outcome result={entry.result} />}
    </Entry>
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
}: {
  entry: SessionEntry;
  botId: string;
  tools: Map<string, string>;
}) {
  switch (entry.kind) {
    case "wake":
      return (
        <Entry label="唤醒" tone="accent" at={entry.at}>
          <ul className="session-messages">
            {entry.messages.map((message) => (
              <MessageLine key={message.message_id} message={message} botId={botId} />
            ))}
          </ul>
          {entry.notice && <p className="small session-warning-text">{entry.notice}</p>}
        </Entry>
      );
    case "history":
      return (
        <Entry label="历史消息" at={entry.at}>
          <ul className="session-messages">
            <MessageLine message={entry.message} botId={botId} />
          </ul>
        </Entry>
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
        <Entry label="模型正文" note="未发送到 QQ" at={entry.at}>
          <p className="entry-text">{entry.text}</p>
        </Entry>
      );
    case "call":
      return <CallEntry entry={entry} tools={tools} />;
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
}: {
  block: Extract<TimelineBlock, { type: "turn" }>;
  current: boolean;
  botId: string;
  tools: Map<string, string>;
}) {
  const boundary = block.entries.find((entry): entry is EntryOf<"boundary"> => entry.kind === "boundary");
  const wake = block.entries.find((entry) => entry.kind === "wake");
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
            <li key={index} className="session-entry">
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
            <EntryView key={index} entry={item.entry} botId={botId} tools={tools} />
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
}: {
  entry: SessionEntry;
  botId: string;
  tools: Map<string, string>;
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
          <span className="entry-label">{entry.origin === "idle" ? "闲时压缩摘要" : "压缩摘要"}</span>
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
      <EntryView entry={entry} botId={botId} tools={tools} />
    </ol>
  );
}

function blockKey(block: TimelineBlock, index: number) {
  const first = block.type === "turn" ? block.entries[0] : block.entry;
  return (block.type === "turn" ? "turn:" + block.turn : first.kind) + ":" + (first.at ?? index);
}

function Timeline({ detail, botId }: { detail: SessionDetail; botId: string }) {
  const tools = useToolTitles();
  const entries = useMemo(() => detail.entries ?? [], [detail.entries]);
  const blocks = useMemo(() => groupEntries(entries), [entries]);
  const end = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(false);
  const seen = useRef("");
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    const check = () => {
      nearBottom.current =
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
      if (nearBottom.current) setFresh(false);
    };
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
    return () => {
      window.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
    };
  }, []);
  const jump = () => {
    end.current?.scrollIntoView({ behavior: prefersReducedMotion() ? "instant" : "smooth", block: "end" });
    setFresh(false);
  };
  // 有新条目时：已在底部附近就跟随到最新；否则提示，不打断正在阅读的位置。
  const marker = detail.session_id + ":" + detail.version;
  useEffect(() => {
    const previous = seen.current;
    seen.current = marker;
    if (!previous || previous === marker) return;
    if (nearBottom.current)
      end.current?.scrollIntoView({ behavior: prefersReducedMotion() ? "instant" : "smooth", block: "end" });
    else setFresh(true);
  }, [marker]);
  if (!entries.length)
    return (
      <EmptyState icon={ScrollText} title={detail.cleared ? "上下文已清空" : "还没有内容"} compact>
        {detail.cleared ? "下次唤醒时会从近期 QQ 历史重新建立上下文。" : "会话建立后，这里会按轮次显示保留的内容。"}
      </EmptyState>
    );
  const lastTurn = blocks.findLast((block) => block.type === "turn");
  return (
    <section className="session-timeline" aria-label="会话上下文">
      <div className="session-timeline-head">
        <h3 className="grow">保留的上下文</h3>
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
            current={
              isBusy(detail.state) &&
              block === lastTurn &&
              !block.entries.some((entry) => entry.kind === "boundary")
            }
          />
        ) : (
          <StandaloneEntry key={blockKey(block, index)} entry={block.entry} botId={botId} tools={tools} />
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

function SessionSummary({ detail, botId, refresh }: { detail: SessionDetail; botId: string; refresh: () => void }) {
  const tools = useToolTitles();
  const context = detail.context;
  const max = context.max_tokens ?? 0;
  const used = context.input_tokens ?? 0;
  const available = detail.available_tools;
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
          <dd className="num">{context.model_request_count ?? 0} 次；压缩请求 {context.compaction_request_count ?? 0} 次</dd>
        </div>
        <div>
          <dt>上下文压缩</dt>
          <dd className="num">{context.compactions ?? 0} 次</dd>
        </div>
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
      {detail.unknown_operations.length > 0 && (
        <div className="session-block session-unknown">
          <h3>
            <TriangleAlert size={15} aria-hidden="true" />
            结果未知的动作 · 已禁止原样重试
          </h3>
          <ul className="session-unknown-list">
            {detail.unknown_operations.map((operation) => (
              <li key={operation.id}>
                <span className="session-tool">{tools.get(operation.tool) ?? operation.tool}</span>
                <span className="small muted">
                  {" → " + (operation.target.kind === "group" ? "群 " : "私聊 ") + operation.target.peer_id}
                </span>
                <JsonBlock label="参数" value={operation.arguments} />
                <div className="row wrap">
                  <Button size="sm" disabled={isBusy(detail.state)} onClick={async () => {
                    if (await resolveOperation(detail.scope_key, operation.id, true)) refresh();
                  }}>核实为已执行</Button>
                  <Button size="sm" disabled={isBusy(detail.state)} onClick={async () => {
                    if (await resolveOperation(detail.scope_key, operation.id, false)) refresh();
                  }}>核实为未执行</Button>
                </div>
              </li>
            ))}
          </ul>
          <p className="tiny muted">重置上下文保留这些记录；核对实际结果后可解除。处理中请先停止本轮。</p>
        </div>
      )}
      {available.length > 0 && (
        <div className="session-block">
          <h3>当前可用工具</h3>
          <span className="chips">
            {available.map((name) => (
              <span key={name} className="chip" title={name}>
                {tools.get(name) ?? name}
              </span>
            ))}
          </span>
        </div>
      )}
    </section>
  );
}

function SessionView({ scopeKey, runtime }: { scopeKey: string; runtime: RuntimeEnvelope | null }) {
  const groups = useGroupNames();
  const summary = runtime?.sessions.find((item) => item.scope_key === scopeKey);
  const preparing = runtime?.turns.some((turn) => turn.scope_key === scopeKey) ?? false;
  const { detail, missing, error, refresh } = useSessionDetail(
    scopeKey,
    [summary?.session_id, summary?.updated_at, summary?.state, summary?.pending, preparing].join("|"),
  );
  const scope = parseScopeKey(scopeKey);
  const back = { to: "/sessions", label: "会话列表" };
  const title = scopeLabel(scopeKey, groups);
  const settings = scope ? conversationRoute(scope.kind, scope.peer) : "/conversations";
  const botId = scope?.bot ?? "";
  if (!detail)
    return (
      <div className="detail">
        <DetailHeader back={back} title={title} />
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
    );
  const busy = isBusy(detail.state);
  return (
    <div className="detail">
      <DetailHeader
        back={back}
        title={title}
        badges={
          <>
            <Badge tone={stateTone(detail.state)}>{turnState(detail.state).text}</Badge>
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
              disabled={detail.cleared && !busy}
              onClick={async () => {
                await resetSession(scopeKey, busy);
                refresh();
              }}
            >
              {busy ? "停止并重置" : "重置上下文"}
            </Button>
            <Button icon={SlidersHorizontal} onClick={() => void navigate(settings)}>
              会话设置
            </Button>
          </>
        }
      />
      {detail.cleared && (
        <Banner tone="accent" icon={RotateCcw}>
          上下文已重置，下次唤醒时重新建立。结果未知的动作和已处理消息的防重记录仍然保留。
        </Banner>
      )}
      {error && (
        <p className="danger-text small" role="alert">
          刷新失败：{error}。下面是上次读取的内容。
        </p>
      )}
      <SessionSummary detail={detail} botId={botId} refresh={refresh} />
      <Timeline detail={detail} botId={botId} />
    </div>
  );
}

export function SessionsPage({ route }: { route: SessionsRoute }) {
  const runtime = useResource(runtimeResource, 3000);
  const rows = useMemo(() => rowsOf(runtime.data), [runtime.data]);
  return (
    <div className="page">
      <PageHeader
        title="会话上下文"
        save={false}
        description="各会话保留在内存中的上下文。推理内容和媒体原始数据不在这里显示；重启后全部清空。"
      />
      <CapacityBar capacity={runtime.data?.capacity} />
      {runtime.error && !runtime.data && (
        <p className="danger-text small" role="alert">
          无法读取运行状态：{runtime.error}
        </p>
      )}
      <MasterDetail label="会话列表" hasDetail={!!route.key} list={<SessionList route={route} rows={rows} />}>
        {route.key ? (
          <SessionView key={route.key} scopeKey={route.key} runtime={runtime.data} />
        ) : (
          <EmptyState icon={ScrollText} title="选择一个会话">
            查看它保留的上下文：唤醒消息、模型正文、工具调用与结果、压缩摘要。
          </EmptyState>
        )}
      </MasterDetail>
    </div>
  );
}
