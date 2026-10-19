import { Plus, RotateCw, Trash2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { confirm } from "@/components/Dialog";
import { Field, Switch } from "@/components/Field";
import { FilterEmpty, IssueBanner, PageHeader } from "@/components/PageHeader";
import { Section, SectionRows } from "@/components/Section";
import { config, useConfig } from "@/lib/config";
import { useFieldFilter } from "@/lib/filter";
import { getIn, pathKey, setIn } from "@/lib/path";
import { revealField } from "@/lib/router";
import { usePageToc, type TocItem } from "@/lib/toc";
import type { JsonObject, PluginId } from "@/lib/types";
import {
  AI_GROUPS_BASE,
  AI_ROWS_REST,
  AI_ROWS_TOP,
  defaultGroup,
  defaultPluginConfig,
  executionSection,
  GROUP_FIELDS,
  pluginMeta,
  PLUGIN_ROWS,
} from "@/schema/plugins";
import type { SectionDef } from "@/schema/types";

import { tocFor } from "./common";

/* ---------- AI 群聊：群列表 ---------- */

function groupList(config: JsonObject): JsonObject[] {
  const groups = getIn(config, AI_GROUPS_BASE);
  return Array.isArray(groups) ? (groups as JsonObject[]) : [];
}

/** 群卡片的稳定 key：增删群时跟着同步，避免输入框状态串到相邻的群。 */
function useStableKeys(count: number) {
  const seq = useRef(count);
  const [keys, setKeys] = useState<number[]>(() => Array.from({ length: count }, (_, index) => index));
  let current = keys;
  if (keys.length !== count) {
    // 外部变化（丢弃、重新载入）导致数量不一致时，按末尾增删对齐。
    current =
      keys.length > count
        ? keys.slice(0, count)
        : [...keys, ...Array.from({ length: count - keys.length }, () => seq.current++)];
    setKeys(current);
  }
  const removeAt = (index: number) => setKeys((list) => list.filter((_, i) => i !== index));
  return { keys: current, removeAt };
}

function GroupCard({ index, onRemove }: { index: number; onRemove: (index: number) => void }) {
  const base = useMemo(() => [...AI_GROUPS_BASE, index], [index]);
  const id = useConfig((state) => {
    const value = getIn(state.draft, [...base, "id"]);
    return value === undefined || value === null ? "" : String(value).trim();
  });
  const title = id ? `群 ${id}` : "新群（还没填群号）";
  return (
    <div className="grp" data-filter-card="" data-search={`${title} ${id}`.toLowerCase()} data-section-path={pathKey(base)}>
      <div className="grp-head">
        <span className="grp-index">#{index + 1}</span>
        <span className="grp-title">{title}</span>
        <code className="grp-path">[[plugins.ai_group_chat.groups]]</code>
        <button
          type="button"
          className="icon-btn icon-btn--danger icon-btn--small"
          aria-label={`删除${title}`}
          title="删除这个群的配置"
          onClick={() =>
            confirm({
              title: `删除${title}？`,
              body: <p>这个群的人格、知识库与上下文预算配置会随自动保存一起移除，无法恢复；群里的对话历史不受影响。</p>,
              confirmLabel: "删除这个群",
              onConfirm: () => onRemove(index),
            })
          }
        >
          <Trash2 className="ic" aria-hidden />
        </button>
      </div>
      <div className="grp-body">
        {GROUP_FIELDS.map((field) => (
          <Field key={field.key} field={field} base={base} />
        ))}
      </div>
    </div>
  );
}

function GroupsSection() {
  const count = useConfig((state) => groupList(state.draft).length);
  const { keys, removeAt } = useStableKeys(count);
  const add = () => {
    config.update((draft) => setIn(draft, AI_GROUPS_BASE, [...groupList(draft), defaultGroup()]), AI_GROUPS_BASE);
    revealField([...AI_GROUPS_BASE, count, "id"]);
  };
  const remove = (index: number) => {
    removeAt(index);
    config.update((draft) => setIn(draft, AI_GROUPS_BASE, groupList(draft).filter((_, i) => i !== index)), AI_GROUPS_BASE);
  };
  return (
    <section
      className="sec sec--groups"
      id="sec-groups"
      data-section="groups"
      data-section-path={pathKey(AI_GROUPS_BASE)}
      data-search="群列表 groups 群号 人格 知识库"
      aria-labelledby="sec-groups-title"
    >
      <header className="sec-head">
        <h2 className="sec-title" id="sec-groups-title">
          群列表
        </h2>
        <code className="sec-path">[[plugins.ai_group_chat.groups]]</code>
        <span className="sec-aside">{count > 0 ? `${count} 个群` : "还没有群"}</span>
        <p className="sec-desc">只有列在这里的群会响应 @；每个群单独配置人格提示词、知识库和长期上下文预算。</p>
      </header>
      <div className="sec-body">
        {keys.map((key, index) => (
          <GroupCard key={key} index={index} onRemove={remove} />
        ))}
        <button type="button" className="grp-add" onClick={add}>
          <Plus className="ic" aria-hidden />
          <span>添加群</span>
          <small>默认人格 maodie.md</small>
        </button>
      </div>
    </section>
  );
}

/* ---------- 执行参数 ---------- */

function ExecutionSection({ id }: { id: PluginId }) {
  const section = useMemo(() => executionSection(id), [id]);
  const present = useConfig((state) => getIn(state.draft, section.base) != null);
  if (present) return <Section section={section} table={`[plugin_execution.plugins.${id}]`} />;
  return (
    <section className="sec" id="sec-execution" data-section="execution" data-section-path={pathKey(section.base)} data-search="执行参数 consumers priority" aria-labelledby="sec-execution-title">
      <header className="sec-head">
        <h2 className="sec-title" id="sec-execution-title">
          执行参数
        </h2>
        <code className="sec-path">[plugin_execution.plugins.{id}]</code>
        <span className="sec-badge">
          <RotateCw className="ic" aria-hidden />
          重启后生效
        </span>
      </header>
      <div className="sec-off">
        mybot.toml 里缺少这个插件的执行参数，没有它进程启动会失败。
        <div>
          <button type="button" className="btn btn--primary" onClick={() => config.set(section.base, { ...pluginMeta(id).execution })}>
            补全执行参数
          </button>
        </div>
      </div>
    </section>
  );
}

/* ---------- 页面 ---------- */

export default function PluginPage({ id }: { id: PluginId }) {
  const meta = pluginMeta(id);
  const enabled = useConfig((state) => getIn(state.draft, ["plugins", id]) != null);
  const [filter, setFilter] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const empty = useFieldFilter(root, filter);
  const isAI = id === "ai_group_chat";
  const top: SectionDef[][] = isAI ? AI_ROWS_TOP : PLUGIN_ROWS[id];
  const rest: SectionDef[][] = isAI ? AI_ROWS_REST : [];
  const execution = useMemo(() => executionSection(id), [id]);

  const toc: TocItem[] = enabled
    ? [
        ...tocFor(top.flat()),
        ...(isAI ? [{ id: "groups", title: "群列表", prefixes: [AI_GROUPS_BASE] }] : []),
        ...tocFor(rest.flat()),
        ...tocFor([execution]),
      ]
    : [{ id: "plugin-off", title: "插件未启用", prefixes: [["plugins", id]] }, ...tocFor([execution])];
  usePageToc(toc);

  const enable = () =>
    config.update((draft) => {
      let next = setIn(draft, ["plugins", id], defaultPluginConfig(id, draft));
      if (getIn(next, execution.base) == null) next = setIn(next, execution.base, { ...meta.execution });
      return next;
    }, ["plugins", id]);
  const disable = () =>
    confirm({
      title: `停用${meta.name}并删除它的配置？`,
      body: (
        <p>
          整个 <code>[plugins.{id}]</code> 配置节会被删除，自动写入后立即生效；再次启用会写入初始配置。执行参数保留不动。
        </p>
      ),
      confirmLabel: "删除配置并停用",
      onConfirm: () => config.set(["plugins", id], null),
    });

  return (
    <div className="page-inner" ref={root}>
      <PageHeader
        page={`plugin:${id}`}
        kicker="插件"
        table={`[plugins.${id}]`}
        title={meta.name}
        desc={`${meta.desc}改动停止 0.8 秒后写入 mybot.toml，插件配置自动热生效。`}
        aside={
          <div className="plugin-toggle">
            <span>{enabled ? "已启用" : "已停用"}</span>
            <Switch checked={enabled} label={enabled ? `停用${meta.name}` : `启用${meta.name}`} onChange={(on) => (on ? enable() : disable())} />
          </div>
        }
        filter={filter}
        onFilter={setFilter}
      />
      <IssueBanner page={`plugin:${id}`} />
      <div className="sections">
        {enabled ? (
          <>
            <SectionRows rows={top} />
            {isAI ? <GroupsSection /> : null}
            <SectionRows rows={rest} />
          </>
        ) : (
          <section className="sec" id="sec-plugin-off" data-section="plugin-off" data-search="插件未启用" aria-labelledby="plugin-off-title">
            <header className="sec-head">
              <h2 className="sec-title" id="plugin-off-title">
                插件未启用
              </h2>
              <code className="sec-path">[plugins.{id}]</code>
              <p className="sec-desc">mybot.toml 里没有这个插件的配置节，插件不会响应任何消息。</p>
            </header>
            <div className="sec-off">
              启用后会写入一份完整的初始配置，写入后立即热生效，之后可以逐项调整。
              <div>
                <button type="button" className="btn btn--primary" onClick={enable}>
                  启用{meta.name}
                </button>
              </div>
            </div>
          </section>
        )}
        <ExecutionSection id={id} />
      </div>
      {empty ? <FilterEmpty query={filter} /> : null}
    </div>
  );
}
