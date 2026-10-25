import { useCallback, useEffect, useState } from "react";
import { errorMessage, post, request } from "../lib/api";
import type { Json, RuntimeEnvelope } from "../lib/types";
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
          {data.turns.map((turn, index) => (
            <div className="activity-row" key={String(turn.turn_id || index)}>
              <span className="dot ok" />
              <div>
                <code>{String(turn.scope_key || "未知会话")}</code>
                <p>
                  {String(turn.state || "运行中")} · 第{" "}
                  {String(turn.rounds || 0)} 轮 · {stamp(turn.started_at)}
                </p>
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
          ))}
          {!data.turns.length && (
            <div className="empty">当前没有活动对话。</div>
          )}
        </div>
      </section>
      <section className="sec">
        <header className="sec-head">
          <div>
            <h2>近期活动</h2>
            <p>只显示结果摘要，不展示思维链。</p>
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
                      {String(item.kind || item.status || "活动")}
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
