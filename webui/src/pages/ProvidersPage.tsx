import { Plus, Trash2, TriangleAlert } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { confirm } from "@/components/Dialog";
import { FilterEmpty, IssueBanner, PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { config, useConfig } from "@/lib/config";
import { useFieldFilter } from "@/lib/filter";
import { cls } from "@/lib/pages";
import { getIn, isRecord, tomlKey } from "@/lib/path";
import { revealField } from "@/lib/router";
import { shallowArrayEqual } from "@/lib/store";
import { usePageToc } from "@/lib/toc";
import type { JsonObject, PathSeg } from "@/lib/types";
import { defaultProvider, PROVIDER_FIELDS } from "@/schema/services";
import { fieldPath, type SectionDef } from "@/schema/types";

const PROVIDER_SECTION: SectionDef = {
  id: "provider",
  title: "Provider",
  base: ["llm", "providers"],
  restart: true,
  desc: "OpenAI 兼容服务。",
  fields: PROVIDER_FIELDS,
};

const REFERENCES: [PathSeg[], string][] = [
  [["plugins", "ai_group_chat", "model", "provider"], "AI 群聊 · 主模型"],
  [["plugins", "ai_group_chat", "vision", "model", "provider"], "AI 群聊 · 视觉模型"],
];

function providerRefs(config: JsonObject, id: string): string[] {
  return REFERENCES.filter(([path]) => getIn(config, path) === id).map(([, label]) => label);
}

function useProviderIds(): string[] {
  return useConfig((state) => {
    const record = getIn(state.draft, ["llm", "providers"]);
    return isRecord(record) ? Object.keys(record) : [];
  }, shallowArrayEqual);
}

function ProviderCard({ id }: { id: string }) {
  const base = useMemo(() => ["llm", "providers", id], [id]);
  const refs = useConfig((state) => providerRefs(state.draft, id), shallowArrayEqual);
  const remove = () =>
    confirm({
      title: `删除 Provider ${id}？`,
      body: (
        <>
          <p>这个 Provider 的 API Key 与连接参数会随自动保存一起从 mybot.toml 移除，无法恢复。</p>
          {refs.length > 0 ? <p>下列模型引用会失效，需要重新选择服务：{refs.join("、")}。</p> : null}
        </>
      ),
      confirmLabel: "删除 Provider",
      onConfirm: () => config.set(base, null),
    });
  return (
    <Section
      section={PROVIDER_SECTION}
      base={base}
      title={id}
      titleMono
      table={`[llm.providers.${tomlKey(id)}]`}
      domId={`provider-${id}`}
      aside={
        <button type="button" className="icon-btn icon-btn--danger icon-btn--small" aria-label={`删除 Provider ${id}`} title="删除这个 Provider" onClick={remove}>
          <Trash2 className="ic" aria-hidden />
        </button>
      }
      footer={refs.length > 0 ? <p className="sec-foot fld-desc">被引用：{refs.join("、")}</p> : null}
    />
  );
}

function AddProvider({ existing }: { existing: string[] }) {
  const [id, setId] = useState("");
  const [error, setError] = useState("");
  const submit = () => {
    if (id.trim() === "") return setError("请输入 Provider ID");
    if (id !== id.trim()) return setError("ID 不能有首尾空格");
    if (existing.includes(id)) return setError(`Provider ${id} 已存在`);
    config.set(["llm", "providers", id], defaultProvider());
    setId("");
    setError("");
    revealField(fieldPath(["llm", "providers", id], PROVIDER_FIELDS[0]!));
  };
  return (
    <section className="sec sec--add" id="sec-provider-add" data-section="provider-add" data-search="新增 provider 添加" aria-labelledby="provider-add-title">
      <header className="sec-head">
        <h2 className="sec-title" id="provider-add-title">
          新增 Provider
        </h2>
        <code className="sec-path">[llm.providers.&lt;id&gt;]</code>
        <p className="sec-desc">ID 是插件引用它的名字，例如 deepseek 或 local.qwen；写入后不能改名，只能删除重建。</p>
      </header>
      <div className="sec-body">
        <div className="add-row">
          <div className="ctl">
            <input
              className={cls("inp is-mono", error && "is-error")}
              value={id}
              placeholder="provider ID，如 deepseek"
              aria-label="新 Provider ID"
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => {
                setId(event.target.value);
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
        ) : (
          <p className="add-hint">新 Provider 写入后要重启进程，插件的模型引用才能选它。</p>
        )}
      </div>
    </section>
  );
}

export default function ProvidersPage() {
  const ids = useProviderIds();
  const [filter, setFilter] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const empty = useFieldFilter(root, filter);
  usePageToc([
    ...ids.map((id) => ({ id: `provider-${id}`, title: id, prefixes: [["llm", "providers", id]] })),
    { id: "provider-add", title: "新增 Provider", prefixes: [] },
  ]);
  return (
    <div className="page-inner" ref={root}>
      <PageHeader
        page="providers"
        kicker="系统"
        table="[llm.providers]"
        title="模型服务"
        desc="OpenAI 兼容的模型服务。插件的模型引用从这里选择 Provider；ID 可以包含点号，界面按完整键名写回。"
        notice="Provider 改动写入后，要重启进程才生效"
        filter={filter}
        onFilter={setFilter}
      />
      <IssueBanner page="providers" />
      <div className="sections">
        {ids.length === 0 ? (
          <div className="empty">
            <p className="empty-title">还没有配置模型服务</p>
            <p className="empty-desc">在下面输入一个 Provider ID 即可添加；AI 群聊从这里选择服务。</p>
          </div>
        ) : (
          ids.map((id) => <ProviderCard key={id} id={id} />)
        )}
        <AddProvider existing={ids} />
      </div>
      {empty ? <FilterEmpty query={filter} /> : null}
    </div>
  );
}
