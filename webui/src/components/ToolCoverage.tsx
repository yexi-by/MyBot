import { useEffect, useState } from "react";
import { request } from "../lib/api";

interface Coverage {
  version: string;
  commit: string;
  source_url: string;
  aliases: string;
  actions: {
    action: string;
    status: string;
    tool: string | null;
    reason: string;
  }[];
}
const labels: Record<string, string> = {
  implemented: "已实现",
  alias: "归并别名",
  adapted: "统一适配",
  internal: "内部使用",
  excluded: "不暴露",
};

export function ToolCoverage({ filter }: { filter: string }) {
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  useEffect(() => {
    let alive = true;
    request<Coverage>("/api/napcat/coverage")
      .then((data) => {
        if (alive) setCoverage(data);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  if (!coverage) return null;
  const counts = coverage.actions.reduce<Record<string, number>>(
    (result, action) => {
      result[action.status] = (result[action.status] || 0) + 1;
      return result;
    },
    {},
  );
  const entries = coverage.actions.filter(
    (action) =>
      !filter ||
      [action.action, action.tool, action.reason]
        .join(" ")
        .toLowerCase()
        .includes(filter.toLowerCase()),
  );
  return (
    <section className="sec" id="section-napcat-coverage">
      <header className="sec-head">
        <div>
          <h2>NapCat 动作覆盖</h2>
          <p>
            锁定版本 {coverage.version} · {coverage.actions.length}{" "}
            项原始动作，各自说明映射或排除原因。
          </p>
        </div>
        <a href={coverage.source_url} target="_blank" rel="noreferrer">
          上游定义 ↗
        </a>
      </header>
      <div className="coverage-body">
        <div className="tool-badges">
          {Object.entries(counts).map(([status, count]) => (
            <span className="badge" key={status}>
              {labels[status] || status} {count}
            </span>
          ))}
        </div>
        <p className="muted">{coverage.aliases}</p>
        <details>
          <summary>展开 {entries.length} 项覆盖说明</summary>
          <div className="coverage-list">
            {entries.map((action) => (
              <div key={action.action}>
                <code>{action.action}</code>
                <span className="badge">
                  {labels[action.status] || action.status}
                </span>
                {action.tool && <code>{action.tool}</code>}
                <p>{action.reason}</p>
              </div>
            ))}
          </div>
        </details>
      </div>
    </section>
  );
}
