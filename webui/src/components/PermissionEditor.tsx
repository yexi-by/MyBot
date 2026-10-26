import { useMemo, useState } from "react";
import { ChevronRight, CircleAlert, ListChecks, RotateCcw, ShieldCheck, ShieldOff } from "lucide-react";
import { change, useConfig } from "../lib/config";
import { applicable, evaluate, overrideOf } from "../lib/permissions";
import type { Override } from "../lib/permissions";
import { toolsResource, useResource } from "../lib/resource";
import { getAt, object } from "../lib/types";
import type { ObjectValue, Path, Tool } from "../lib/types";
import { Menu } from "./Menu";
import { Badge, Banner, Button, EmptyState, Segmented, Spinner, StatusDot, Switch } from "./primitives";
import { SearchInput } from "./shell";
import { ToolDrawer } from "./ToolDrawer";

type Filter = "all" | "allowed" | "effects";
const overrides: { value: Override; label: string }[] = [
  { value: "inherit", label: "跟随" },
  { value: "allow", label: "允许" },
  { value: "deny", label: "禁止" },
];

function matches(tool: Tool, text: string) {
  return !text || (tool.title + " " + tool.name).toLowerCase().includes(text);
}

/** 会话的能力权限：分类开关、单个工具的允许 / 禁止，以及参数限制入口。 */
export function PermissionEditor({ base, kind }: { base: Path; kind: "group" | "private" }) {
  const state = useConfig();
  const tools = useResource(toolsResource);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const conversation = object(getAt(state.draft, base));
  const policy = object(conversation.policy);
  const policyPath = [...base, "policy"];
  const all = useMemo(
    () => (tools.data?.tools ?? []).filter((tool) => tool.category !== "harness"),
    [tools.data],
  );
  const catalog = useMemo(() => new Map((tools.data?.tools ?? []).map((tool) => [tool.name, tool])), [tools.data]);
  const labels = useMemo(
    () => new Map((tools.data?.categories ?? []).map((category) => [category.id, category.label])),
    [tools.data],
  );

  if (!tools.data)
    return tools.error ? (
      <Banner
        tone="danger"
        icon={CircleAlert}
        title="无法读取工具目录"
        actions={
          <Button size="sm" onClick={() => void toolsResource.refresh()}>
            重试
          </Button>
        }
      >
        {tools.error}
      </Banner>
    ) : (
      <Spinner label="正在读取工具目录" />
    );

  const usable = all.filter((tool) => applicable(tool, kind));
  const hidden = all.length - usable.length;
  const access = new Map(usable.map((tool) => [tool.name, evaluate(state.draft, conversation, kind, tool, catalog)]));
  const allowedCount = usable.filter((tool) => access.get(tool.name)?.allowed).length;
  const text = query.trim().toLowerCase();
  const categories = (tools.data.categories ?? []).map((category) => category.id);
  for (const tool of usable) if (!categories.includes(tool.category)) categories.push(tool.category);
  const toolOverrides = object(policy.tools);
  const parameterRules = object(policy.parameters);

  const setOverrides = (names: string[], value: Override) => {
    const next: ObjectValue = { ...toolOverrides };
    for (const name of names) {
      if (value === "inherit") delete next[name];
      else next[name] = value === "allow";
    }
    change([...policyPath, "tools"], next);
  };
  const toggle = (id: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const groupsToShow = categories
    .map((id) => {
      const members = usable.filter((tool) => tool.category === id);
      const visible = members.filter(
        (tool) =>
          matches(tool, text) &&
          (filter !== "allowed" || access.get(tool.name)?.allowed) &&
          (filter !== "effects" || tool.effects),
      );
      return { id, members, visible };
    })
    .filter((group) => group.members.length && (group.visible.length || (!text && filter === "all")));
  const narrowed = !!text || filter !== "all";
  const selectedTool = selected ? (catalog.get(selected) ?? null) : null;

  return (
    <div className="permissions">
      {conversation.enabled !== true && (
        <Banner tone="warning" icon={CircleAlert}>
          会话未启用，以下权限在启用后才会生效。
        </Banner>
      )}
      <div className="toolbar">
        <SearchInput value={query} onChange={setQuery} placeholder="搜索工具名称" label="搜索工具" />
        <Segmented
          size="sm"
          label="筛选工具"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "全部" },
            { value: "allowed", label: "可调用" },
            { value: "effects", label: "会执行操作" },
          ]}
        />
        <span className="grow" />
        <span className="muted small num">
          可调用 {allowedCount} / {usable.length}
        </span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            setExpanded(expanded.size ? new Set() : new Set(groupsToShow.map((group) => group.id)))
          }
        >
          {expanded.size ? "全部收起" : "全部展开"}
        </Button>
      </div>
      {groupsToShow.length === 0 ? (
        <EmptyState icon={ListChecks} title="没有符合条件的工具" compact>
          调整搜索词或筛选条件后再试。
        </EmptyState>
      ) : (
        <div className="perm-list card">
          {groupsToShow.map(({ id, members, visible }) => {
            const label = labels.get(id) ?? id;
            const open = narrowed || expanded.has(id);
            const enabled = object(policy.categories)[id] === true;
            const allowed = members.filter((tool) => access.get(tool.name)?.allowed).length;
            const custom = members.filter((tool) => Object.hasOwn(toolOverrides, tool.name)).length;
            const names = members.map((tool) => tool.name);
            const effects = members.filter((tool) => tool.effects).length;
            const mixed = effects > 0 && effects < members.length;
            return (
              <section className="perm-category" key={id}>
                <div className="perm-category-row">
                  <button
                    type="button"
                    className="perm-toggle"
                    aria-expanded={open}
                    onClick={() => toggle(id)}
                    disabled={narrowed}
                  >
                    <ChevronRight size={16} className={"chevron" + (open ? " open" : "")} aria-hidden="true" />
                    <span className="perm-category-name">{label}</span>
                    <span className="muted small num">
                      {members.length} 个工具 · 可调用 {allowed}
                      {custom ? " · " + custom + " 项单独设置" : ""}
                    </span>
                    {effects > 0 && <Badge tone="warning">{mixed ? "含操作类工具" : "会执行操作"}</Badge>}
                  </button>
                  <Menu
                    label={label + "的批量操作"}
                    items={[
                      { label: "全部允许", icon: ShieldCheck, onSelect: () => setOverrides(names, "allow") },
                      { label: "全部禁止", icon: ShieldOff, onSelect: () => setOverrides(names, "deny") },
                      {
                        label: "全部恢复跟随分类",
                        icon: RotateCcw,
                        disabled: !custom,
                        onSelect: () => setOverrides(names, "inherit"),
                      },
                    ]}
                  />
                  <Switch
                    checked={enabled}
                    label={"开放" + label}
                    onChange={(next) => change([...policyPath, "categories", id], next)}
                  />
                </div>
                {open && (
                  <ul className="perm-tools">
                    {visible.map((tool) => {
                      const result = access.get(tool.name)!;
                      const rules = Object.keys(object(parameterRules[tool.name])).length;
                      return (
                        <li className="perm-tool" key={tool.name}>
                          <button type="button" className="perm-tool-main" onClick={() => setSelected(tool.name)}>
                            <span className="perm-tool-title">{tool.title}</span>
                            <span className="perm-tool-name mono">{tool.name}</span>
                          </button>
                          <span className="perm-tool-badges">
                            {mixed && tool.effects && <Badge tone="warning">会执行操作</Badge>}
                            {rules > 0 && <Badge tone="accent">参数限制 {rules}</Badge>}
                          </span>
                          <Segmented
                            size="sm"
                            label={tool.title + "的权限"}
                            value={overrideOf(policy, tool.name)}
                            options={overrides}
                            onChange={(value) => setOverrides([tool.name], value)}
                          />
                          <span
                            className={"perm-effect" + (result.allowed ? " on" : "")}
                            title={result.allowed ? "可以调用" : result.reason}
                          >
                            <StatusDot tone={result.allowed ? "success" : "neutral"} />
                            <span className="perm-effect-text">{result.allowed ? "可调用" : result.reason}</span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}
      {hidden > 0 && (
        <p className="muted small">
          另有 {hidden} 个工具只适用于{kind === "private" ? "群聊" : "私聊"}，未在这里列出。
        </p>
      )}
      <ToolDrawer
        tool={selectedTool}
        categoryLabel={selectedTool ? (labels.get(selectedTool.category) ?? selectedTool.category) : ""}
        onClose={() => setSelected(null)}
        context={{ base, kind }}
        catalog={catalog}
      />
    </div>
  );
}
