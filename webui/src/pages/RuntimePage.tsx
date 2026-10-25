import { useCallback, useEffect, useState } from "react";
import { errorMessage, post, request } from "../lib/api";
import type { Json, ObjectValue, RuntimeEnvelope } from "../lib/types";
import { object } from "../lib/types";
import { notify } from "../lib/ui";
import { Section } from "../components/Forms";
import { runtimeSection } from "../schema/fields";
function stamp(value: Json | undefined) {
  if (!value) return "—";
  const date =
    typeof value === "number"
      ? new Date(value * 1000)
      : new Date(String(value));
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleTimeString("zh-CN", { hour12: false });
}
function count(value: Json | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}
function formatCount(value: number | null) {
  return value === null ? "—" : Math.round(value).toLocaleString("zh-CN");
}
const turnStates: Record<string, string> = {
  queued: "等待执行",
  running: "运行中",
  compacting: "正在压缩上下文",
};
function ContextUsage({
  context,
  scope,
}: {
  context: ObjectValue;
  scope: string;
}) {
  const input = count(context.input_tokens),
    maximum = count(context.max_tokens),
    trigger = count(context.trigger_tokens),
    compactions = count(context.compactions),
    percent =
      input !== null && maximum !== null && maximum > 0
        ? Math.round((input / maximum) * 100)
        : null;
  const source =
    context.usage_source === "api_calibrated"
      ? "API usage 校准估算"
      : context.usage_source === "estimate"
        ? "本地估算"
        : "估算来源待确认";
  return (
    <div className="turn-context">
      <div className="context-summary">
        <span>
          上下文约{" "}
          <strong>
            {formatCount(input)} / {formatCount(maximum)}
          </strong>{" "}
          token
        </span>
        <span className="badge">{source}</span>
      </div>
      {percent !== null && input !== null && maximum !== null && (
        <div
          className={
            "context-meter" +
            (trigger !== null && input >= trigger ? " near-limit" : "")
          }
        >
          <progress
            value={Math.min(input, maximum)}
            max={maximum}
            aria-label={scope + " 上下文占用估算"}
            aria-valuetext={
              "约 " +
              formatCount(input) +
              " / " +
              formatCount(maximum) +
              " token，" +
              source
            }
          />
          <span aria-hidden="true" />
        </div>
      )}
      <div className="context-details">
        <span>
          自动压缩线 {formatCount(trigger)} token（80%）
          {percent !== null ? " · 当前约 " + percent + "%" : ""}
        </span>
        <span>已压缩 {formatCount(compactions)} 次</span>
      </div>
    </div>
  );
}
export function RuntimePage({ filter }: { filter: string }) {
  const [data, setData] = useState<RuntimeEnvelope>({
      napcat: null,
      turns: [],
      activity: [],
    }),
    [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await request<RuntimeEnvelope>("/api/runtime"));
      setError("");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [load]);
  const napcat = object(data.napcat);
  return (
    <>
      <p className="notice">
        这里只展示内存中的当前活动。重启后清空，不恢复聊天或执行队列。
      </p>
      <p className="notice">
        上下文占用达到模型容量的 80% 时自动压缩，目标约
        50%，保留最近完整工具轮次。token 用量为估算，可由接口 usage 校准。
      </p>
      {error && <p className="notice bad">{error}</p>}
      <div className="stat-grid">
        <div className="stat">
          <span>活动会话</span>
          <strong>{data.turns.length}</strong>
          <small>同一会话串行</small>
        </div>
        <div className="stat">
          <span>近期活动</span>
          <strong>{data.activity.length}</strong>
          <small>只在内存中保留</small>
        </div>
        <div className="stat">
          <span>NapCat</span>
          <strong className="stat-word">
            {Array.isArray(napcat.connected) && napcat.connected.length
              ? "已接入"
              : "等待连接"}
          </strong>
          <small>数据从上游按需获取</small>
        </div>
      </div>
      <section className="sec">
        <header className="sec-head">
          <div>
            <h2>正在进行</h2>
            <p>停止后不再提交新动作，已经发送的消息不会撤销。</p>
          </div>
          <button className="small" onClick={() => void load()}>
            刷新
          </button>
        </header>
        <div className="activity-list">
          {data.turns.map((turn, index) => {
            const context = object(turn.context),
              scope = String(turn.scope_key || "未知会话"),
              requests = count(context.request_count),
              state = String(turn.state || "running");
            return (
              <div
                className={
                  "activity-row turn-row" +
                  (state === "compacting" ? " compacting" : "")
                }
                key={String(turn.turn_id || index)}
              >
                <span className="dot ok" />
                <div>
                  <div className="activity-meta">
                    <code>{scope}</code>
                    <span className="badge">{turnStates[state] || state}</span>
                  </div>
                  <p>
                    {stamp(turn.started_at)}
                    {requests !== null
                      ? " · 已请求 " +
                        formatCount(requests) +
                        " 次（含压缩和恢复）"
                      : ""}
                  </p>
                  {Object.keys(context).length ? (
                    <ContextUsage context={context} scope={scope} />
                  ) : (
                    <p className="context-pending">
                      {state === "queued"
                        ? "排队中，尚未准备上下文。"
                        : "正在准备上下文估算。"}
                    </p>
                  )}
                </div>
                <button
                  className="danger small"
                  onClick={async () => {
                    try {
                      await post("/api/runtime/cancel", {
                        scope_key: String(turn.scope_key),
                      });
                      await load();
                    } catch (reason) {
                      notify(errorMessage(reason));
                    }
                  }}
                >
                  停止
                </button>
              </div>
            );
          })}
          {!data.turns.length && (
            <div className="empty">当前没有活动对话。</div>
          )}
        </div>
      </section>
      <section className="sec">
        <header className="sec-head">
          <div>
            <h2>近期活动</h2>
            <p>只显示结果摘要；上下文 token 数为估算，不展示思维链。</p>
          </div>
          <span className="badge">暂存</span>
        </header>
        <div className="activity-list">
          {data.activity
            .filter(
              (item) =>
                !filter ||
                [item.message, item.tool, item.scope_key, item.kind]
                  .join(" ")
                  .toLowerCase()
                  .includes(filter.toLowerCase()),
            )
            .map((item, index) => (
              <div className="activity-row" key={String(item.id || index)}>
                <time>{stamp(item.time)}</time>
                <div>
                  <div className="activity-meta">
                    <code>{String(item.scope_key || "系统")}</code>
                    <span
                      className={
                        "badge " + (item.status === "error" ? "bad" : "")
                      }
                    >
                      {item.kind === "compaction"
                        ? "上下文压缩"
                        : String(item.kind || item.status || "活动")}
                    </span>
                    {item.tool && <code>{String(item.tool)}</code>}
                  </div>
                  <p>{String(item.message || "")}</p>
                </div>
              </div>
            ))}
          {!data.activity.length && (
            <div className="empty">新的运行结果会显示在这里。</div>
          )}
        </div>
      </section>
      <Section definition={runtimeSection} base={["runtime"]} filter={filter} />
    </>
  );
}
