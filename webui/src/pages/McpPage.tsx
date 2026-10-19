import { Plus, Trash2, TriangleAlert } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { confirm } from "@/components/Dialog";
import { FilterEmpty, IssueBanner, PageHeader } from "@/components/PageHeader";
import { Section, SectionRows } from "@/components/Section";
import { config, useConfig } from "@/lib/config";
import { useFieldFilter } from "@/lib/filter";
import { cls } from "@/lib/pages";
import { getIn, isRecord, setIn, tomlKey } from "@/lib/path";
import { revealField } from "@/lib/router";
import { shallowArrayEqual } from "@/lib/store";
import { usePageToc } from "@/lib/toc";
import type { JsonObject } from "@/lib/types";
import {
  defaultMcpServer,
  MCP_DISABLED,
  MCP_HTTP_FIELDS,
  MCP_SECTION,
  MCP_STDIO_FIELDS,
  MCP_TRANSPORT,
  type McpTransport,
} from "@/schema/services";
import { fieldPath, type FieldDef, type SectionDef } from "@/schema/types";

import { tocFor } from "./common";

const SERVER_SECTIONS: Record<McpTransport, SectionDef> = {
  stdio: {
    id: "mcp-server",
    title: "MCP 服务",
    base: ["mcp", "servers"],
    restart: true,
    desc: "本地进程：MyBot 启动命令并通过标准输入输出通信。",
    fields: [],
  },
  streamable_http: {
    id: "mcp-server",
    title: "MCP 服务",
    base: ["mcp", "servers"],
    restart: true,
    desc: "HTTP 服务：连接远端的 Streamable HTTP 端点。",
    fields: [],
  },
};

/** 切换连接方式时记住另一种方式已填的内容，切回来可以恢复。 */
const transportMemory = new Map<string, Partial<Record<McpTransport, JsonObject>>>();

function switchTransport(draft: JsonObject, name: string, next: unknown): JsonObject {
  const base = ["mcp", "servers", name];
  const current = getIn(draft, base);
  if (!isRecord(current)) return draft;
  const from: McpTransport = current.transport === "streamable_http" ? "streamable_http" : "stdio";
  const to: McpTransport = next === "streamable_http" ? "streamable_http" : "stdio";
  if (from === to) return draft;
  const memory = transportMemory.get(name) ?? {};
  memory[from] = current as JsonObject;
  transportMemory.set(name, memory);
  const disabled = current.disabled === true;
  const restored = memory[to];
  return setIn(draft, base, restored ? { ...restored, transport: to, disabled } : defaultMcpServer(to, disabled));
}

function ServerCard({ name }: { name: string }) {
  const base = useMemo(() => ["mcp", "servers", name], [name]);
  const transport = useConfig((state): McpTransport =>
    getIn(state.draft, [...base, "transport"]) === "streamable_http" ? "streamable_http" : "stdio",
  );
  const disabled = useConfig((state) => getIn(state.draft, [...base, "disabled"]) === true);
  const fields = useMemo<FieldDef[]>(
    () => [
      { ...MCP_TRANSPORT, apply: (draft, value) => switchTransport(draft, name, value) },
      MCP_DISABLED,
      ...(transport === "stdio" ? MCP_STDIO_FIELDS : MCP_HTTP_FIELDS),
    ],
    [name, transport],
  );
  const remove = () =>
    confirm({
      title: `删除 MCP 服务 ${name}？`,
      body: <p>这个服务的连接配置（含环境变量与请求头）会随自动保存一起移除，无法恢复。</p>,
      confirmLabel: "删除服务",
      onConfirm: () => {
        transportMemory.delete(name);
        config.set(base, null);
      },
    });
  return (
    <Section
      section={SERVER_SECTIONS[transport]}
      base={base}
      fields={fields}
      title={disabled ? `${name}（已禁用）` : name}
      titleMono
      table={`[mcp.servers.${tomlKey(name)}]`}
      domId={`mcp-${name}`}
      aside={
        <button type="button" className="icon-btn icon-btn--danger icon-btn--small" aria-label={`删除 MCP 服务 ${name}`} title="删除这个服务" onClick={remove}>
          <Trash2 className="ic" aria-hidden />
        </button>
      }
    />
  );
}

function AddServer({ existing }: { existing: string[] }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const submit = () => {
    if (name.trim() === "") return setError("请输入服务名");
    if (name !== name.trim()) return setError("服务名不能有首尾空格");
    if (existing.includes(name)) return setError(`服务 ${name} 已存在`);
    config.set(["mcp", "servers", name], defaultMcpServer("stdio"));
    setName("");
    setError("");
    revealField(fieldPath(["mcp", "servers", name], MCP_STDIO_FIELDS[0]!));
  };
  return (
    <section className="sec sec--add" id="sec-mcp-add" data-section="mcp-add" data-search="新增 mcp 服务 添加" aria-labelledby="mcp-add-title">
      <header className="sec-head">
        <h2 className="sec-title" id="mcp-add-title">
          新增 MCP 服务
        </h2>
        <code className="sec-path">[mcp.servers.&lt;name&gt;]</code>
        <p className="sec-desc">服务名会出现在工具名里（mcp__服务名__工具名），默认按本地进程添加，之后可以切换为 HTTP 服务。</p>
      </header>
      <div className="sec-body">
        <div className="add-row">
          <div className="ctl">
            <input
              className={cls("inp is-mono", error && "is-error")}
              value={name}
              placeholder="服务名，如 firecrawl"
              aria-label="新 MCP 服务名"
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => {
                setName(event.target.value);
                if (error) setError("");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  submit();
                }
              }}
            />
          </div>
          <button type="button" className="btn btn--primary" onClick={submit}>
            <Plus className="ic" aria-hidden />
            添加
          </button>
        </div>
        {error ? (
          <p className="fld-err" role="alert">
            <TriangleAlert className="ic" aria-hidden />
            {error}
          </p>
        ) : null}
      </div>
    </section>
  );
}

export default function McpPage() {
  const names = useConfig((state) => {
    const record = getIn(state.draft, ["mcp", "servers"]);
    return isRecord(record) ? Object.keys(record) : [];
  }, shallowArrayEqual);
  const [filter, setFilter] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const empty = useFieldFilter(root, filter);
  usePageToc([
    ...tocFor([MCP_SECTION]),
    ...names.map((name) => ({ id: `mcp-${name}`, title: name, prefixes: [["mcp", "servers", name]] })),
    { id: "mcp-add", title: "新增 MCP 服务", prefixes: [] },
  ]);
  return (
    <div className="page-inner" ref={root}>
      <PageHeader
        page="mcp"
        kicker="系统"
        table="[mcp]"
        title="MCP 服务"
        desc="管理 stdio 本地进程和 Streamable HTTP 服务；服务名、环境变量名与请求头名都按原样写回。"
        notice="MCP 改动写入后，要重启进程才生效"
        filter={filter}
        onFilter={setFilter}
      />
      <IssueBanner page="mcp" />
      <div className="sections">
        <SectionRows rows={[[MCP_SECTION]]} />
        {names.length === 0 ? (
          <div className="empty">
            <p className="empty-title">还没有 MCP 服务</p>
            <p className="empty-desc">在下面输入服务名即可添加；工具会以 mcp__服务名__工具名 的形式提供给 AI 调用。</p>
          </div>
        ) : (
          names.map((name) => <ServerCard key={name} name={name} />)
        )}
        <AddServer existing={names} />
      </div>
      {empty ? <FilterEmpty query={filter} /> : null}
    </div>
  );
}
