import { useEffect, useMemo, useState } from "react";
import {
  CircleAlert,
  Copy,
  Hash,
  ListChecks,
  MessagesSquare,
  Plus,
  RefreshCw,
  Trash2,
  User,
} from "lucide-react";
import { Combobox } from "../components/Combobox";
import { Dialog } from "../components/Dialog";
import { ConfigSection } from "../components/form";
import { Menu } from "../components/Menu";
import { PermissionEditor } from "../components/PermissionEditor";
import { Banner, Button, EmptyState, StatusDot, Switch, Tabs } from "../components/primitives";
import { CopyButton, DetailHeader, MasterDetail, PageHeader, SearchInput } from "../components/shell";
import { change, changeMany, useConfig } from "../lib/config";
import { conversationRoute, groupTitle } from "../lib/conversations";
import type { ConversationTab } from "../lib/conversations";
import { formatNumber } from "../lib/format";
import { conversationRefs, hasSessionModel, modelChanges, modelParts } from "../lib/models";
import type { ConversationRef } from "../lib/models";
import { linkHandler, navigate, useLocation } from "../lib/navigation";
import { groupsResource, reloadGroups, useGroupNames, useResource } from "../lib/resource";
import { getAt, object } from "../lib/types";
import type { Json, ObjectValue, Path } from "../lib/types";
import { confirm, toast } from "../lib/ui";
import type { Route } from "../routes";
import {
  compactionModelSection,
  defaultConversation,
  idleCompactionSection,
  limitsSection,
  sessionModelSection,
  wakeSection,
} from "../schema/fields";

type ConversationRoute = Extract<Route, { page: "conversations" }>;
const groupIdPattern = /^\d{5,15}$/;
const allParts = () => modelParts.map((part) => part.id);
const refKey = (ref: ConversationRef) => ref.base.join(":");

/** 会话当前选择的模型名称，用于列表中的说明。 */
function modelName(config: ObjectValue, base: Path) {
  const name = getAt(config, [...base, "model", "name"]);
  return typeof name === "string" && name ? name : "未选模型";
}

function AddGroupDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const state = useConfig();
  const groups = useGroupNames();
  const directory = useResource(groupsResource, open ? 60_000 : 0);
  const [id, setId] = useState("");
  const [enable, setEnable] = useState(true);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [modelSource, setModelSource] = useState("");
  const sources = conversationRefs(state.draft, groups).filter((ref) =>
    hasSessionModel(state.draft, object(getAt(state.draft, ref.base))),
  );
  useEffect(() => {
    if (open) {
      setId("");
      setQuery("");
      setEnable(true);
      // 默认沿用第一个已选好模型的会话，免得新群加进来就不回复。
      setModelSource(sources[0] ? refKey(sources[0]) : "");
    }
  }, [open]);
  const configured = object(state.draft.groups);
  const value = id.trim();
  const error = !value
    ? ""
    : !groupIdPattern.test(value)
      ? "群号应为 5 到 15 位数字"
      : Object.hasOwn(configured, value)
        ? "这个群已经在列表中"
        : "";
  const text = query.trim().toLowerCase();
  const candidates = (directory.data?.groups ?? []).filter(
    (group) =>
      !Object.hasOwn(configured, group.group_id) &&
      (!text || (group.group_name + " " + group.group_id).toLowerCase().includes(text)),
  );
  const add = (groupId: string) => {
    if (!groupIdPattern.test(groupId) || Object.hasOwn(configured, groupId)) return;
    const source = sources.find((ref) => refKey(ref) === modelSource);
    const base: Path = ["groups", groupId];
    changeMany([
      [base, { ...defaultConversation(), enabled: enable }],
      ...(source ? modelChanges(object(getAt(state.draft, source.base)), [base], allParts()) : []),
    ]);
    onClose();
    void navigate(conversationRoute("group", groupId, source ? "general" : "model"));
    toast("已添加群 " + groupId + (enable ? "，已启用" : ""), "success");
  };
  return (
    <Dialog
      open={open}
      title="添加群"
      description="新群默认开放读取消息、发送消息和成员资料，可在添加后调整。"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={!value || !!error} onClick={() => add(value)}>
            添加
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field-stacked">
          <span className="field-label">群号</span>
          <input
            className="input mono"
            value={id}
            inputMode="numeric"
            placeholder="例如 123456789"
            aria-invalid={!!error || undefined}
            autoComplete="off"
            onChange={(event) => setId(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && value && !error) add(value);
            }}
          />
          {error && <span className="field-error">{error}</span>}
        </label>
        <label className="check-row">
          <input type="checkbox" checked={enable} onChange={(event) => setEnable(event.target.checked)} />
          添加后立即启用
        </label>
        <div className="field-stacked">
          <span className="field-label">模型设置</span>
          <Combobox
            label="模型设置"
            value={modelSource}
            onChange={setModelSource}
            options={[
              { value: "", label: "不复制，稍后选择" },
              ...sources.map((ref) => ({
                value: refKey(ref),
                label: "复制自" + ref.label + " · " + modelName(state.draft, ref.base),
              })),
            ]}
          />
          <span className="field-help">复制会话模型、闲时压缩和压缩模型；添加后仍可单独修改。</span>
        </div>
        <section className="picker">
          <div className="picker-head">
            <span className="field-label grow">从机器人已加入的群中选择</span>
            <Button
              size="sm"
              variant="ghost"
              icon={RefreshCw}
              loading={loading}
              onClick={async () => {
                setLoading(true);
                await reloadGroups();
                setLoading(false);
              }}
            >
              刷新
            </Button>
          </div>
          {directory.data?.available ? (
            <>
              <SearchInput value={query} onChange={setQuery} placeholder="按群名或群号筛选" label="筛选群" pageSearch={false} />
              <ul className="picker-list">
                {candidates.length ? (
                  candidates.slice(0, 100).map((group) => (
                    <li key={group.group_id}>
                      <button
                        type="button"
                        className={"picker-item" + (value === group.group_id ? " selected" : "")}
                        onClick={() => setId(group.group_id)}
                        onDoubleClick={() => add(group.group_id)}
                      >
                        <span className="truncate">{group.group_name || "未命名群"}</span>
                        <span className="muted mono small">{group.group_id}</span>
                        {group.member_count !== null && (
                          <span className="muted small num">{formatNumber(group.member_count)} 人</span>
                        )}
                      </button>
                    </li>
                  ))
                ) : (
                  <li className="muted small picker-empty">
                    {text ? "没有匹配的群" : "机器人加入的群都已在列表中"}
                  </li>
                )}
              </ul>
            </>
          ) : (
            <p className="muted small">
              {directory.data?.reason || directory.error || "正在读取群列表…"}
              {directory.data && !directory.data.available && "。可以直接输入群号添加。"}
            </p>
          )}
        </section>
      </div>
    </Dialog>
  );
}

function CopyPolicyDialog({
  open,
  onClose,
  target,
  targetLabel,
}: {
  open: boolean;
  onClose: () => void;
  target: Path;
  targetLabel: string;
}) {
  const state = useConfig();
  const groups = useGroupNames();
  const [source, setSource] = useState("");
  useEffect(() => {
    if (open) setSource("");
  }, [open]);
  const options = [
    ...(target[0] === "private" ? [] : [{ value: "private", label: "私聊" }]),
    ...Object.keys(object(state.draft.groups))
      .filter((id) => !(target[0] === "groups" && target[1] === id))
      .map((id) => ({ value: "group:" + id, label: groupTitle(id, groups) + "（" + id + "）" })),
  ];
  const sourcePath: Path | null = !source
    ? null
    : source === "private"
      ? ["private", "policy"]
      : ["groups", source.slice(6), "policy"];
  const policy = sourcePath ? object(getAt(state.draft, sourcePath)) : {};
  const count = (value: Json | undefined) => Object.keys(object(value)).length;
  return (
    <Dialog
      open={open}
      size="sm"
      title="从其他会话复制权限"
      description={"复制分类开关、单个工具设置和参数限制，替换" + targetLabel + "当前的能力权限。发送上限和跨会话设置不变。"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={!sourcePath}
            onClick={() => {
              if (!sourcePath) return;
              const next: ObjectValue = {
                ...object(getAt(state.draft, [...target, "policy"])),
                categories: structuredClone(object(policy.categories)),
                tools: structuredClone(object(policy.tools)),
                parameters: structuredClone(object(policy.parameters)),
              };
              change([...target, "policy"], next);
              onClose();
              toast("已复制能力权限", "success");
            }}
          >
            复制并替换
          </Button>
        </>
      }
    >
      <div className="stack">
        <Combobox label="复制来源" value={source} options={options} onChange={setSource} placeholder="选择会话" />
        {sourcePath && (
          <p className="secondary small">
            将复制 {Object.values(object(policy.categories)).filter((value) => value === true).length} 个已开放分类、
            {count(policy.tools)} 项单独设置、{count(policy.parameters)} 个工具的参数限制。
          </p>
        )}
      </div>
    </Dialog>
  );
}

function ModelPartsPicker({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  return (
    <fieldset className="model-parts">
      <legend className="field-label">复制哪些设置</legend>
      {modelParts.map((part) => (
        <label className="check-row" key={part.id}>
          <input
            type="checkbox"
            checked={value.includes(part.id)}
            onChange={(event) =>
              onChange(event.target.checked ? [...value, part.id] : value.filter((item) => item !== part.id))
            }
          />
          {part.label}
        </label>
      ))}
    </fieldset>
  );
}

/** 把当前会话的模型设置一次写到多个会话。 */
function ApplyModelDialog({
  open,
  onClose,
  source,
}: {
  open: boolean;
  onClose: () => void;
  source: ConversationRef;
}) {
  const state = useConfig();
  const groups = useGroupNames();
  const [parts, setParts] = useState<string[]>(allParts);
  const [targets, setTargets] = useState<Set<string>>(() => new Set());
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (open) {
      setParts(allParts());
      setTargets(new Set());
      setQuery("");
    }
  }, [open]);
  const refs = conversationRefs(state.draft, groups).filter((ref) => refKey(ref) !== refKey(source));
  const text = query.trim().toLowerCase();
  const visible = refs.filter((ref) => !text || (ref.label + " " + ref.id).toLowerCase().includes(text));
  const allSelected = visible.length > 0 && visible.every((ref) => targets.has(refKey(ref)));
  const toggle = (keys: string[], on: boolean) => {
    const next = new Set(targets);
    for (const key of keys) {
      if (on) next.add(key);
      else next.delete(key);
    }
    setTargets(next);
  };
  const apply = () => {
    const chosen = refs.filter((ref) => targets.has(refKey(ref)));
    changeMany(
      modelChanges(
        object(getAt(state.draft, source.base)),
        chosen.map((ref) => ref.base),
        parts,
      ),
    );
    onClose();
    toast("已应用到 " + chosen.length + " 个会话", "success");
  };
  return (
    <Dialog
      open={open}
      title="应用到其他会话"
      description={"把" + source.label + "的所选模型设置复制到勾选的会话，替换它们对应的设置。"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={!targets.size || !parts.length} onClick={apply}>
            {targets.size ? "应用到 " + targets.size + " 个会话" : "应用"}
          </Button>
        </>
      }
    >
      <div className="stack">
        <ModelPartsPicker value={parts} onChange={setParts} />
        <section className="picker">
          <div className="picker-head">
            <span className="field-label grow">目标会话</span>
            <Button
              size="sm"
              variant="ghost"
              disabled={!visible.length}
              onClick={() => toggle(visible.map(refKey), !allSelected)}
            >
              {allSelected ? "取消全选" : "全选"}
            </Button>
          </div>
          {refs.length > 6 && (
            <SearchInput value={query} onChange={setQuery} placeholder="按群名或群号筛选" label="筛选会话" pageSearch={false} />
          )}
          <ul className="picker-list">
            {visible.length ? (
              visible.map((ref) => (
                <li key={refKey(ref)}>
                  <label className="picker-item picker-check">
                    <input
                      type="checkbox"
                      checked={targets.has(refKey(ref))}
                      onChange={(event) => toggle([refKey(ref)], event.target.checked)}
                    />
                    <span className="truncate">{ref.label}</span>
                    <span className="muted mono small truncate">{modelName(state.draft, ref.base)}</span>
                  </label>
                </li>
              ))
            ) : (
              <li className="muted small picker-empty">{text ? "没有匹配的会话" : "没有其他会话"}</li>
            )}
          </ul>
        </section>
      </div>
    </Dialog>
  );
}

function CopyModelDialog({
  open,
  onClose,
  target,
}: {
  open: boolean;
  onClose: () => void;
  target: ConversationRef;
}) {
  const state = useConfig();
  const groups = useGroupNames();
  const [source, setSource] = useState("");
  const [parts, setParts] = useState<string[]>(allParts);
  useEffect(() => {
    if (open) {
      setSource("");
      setParts(allParts());
    }
  }, [open]);
  const refs = conversationRefs(state.draft, groups).filter((ref) => refKey(ref) !== refKey(target));
  const chosen = refs.find((ref) => refKey(ref) === source);
  return (
    <Dialog
      open={open}
      size="sm"
      title="从其他会话复制模型设置"
      description={"用所选会话的设置替换" + target.label + "的对应设置。"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={!chosen || !parts.length}
            onClick={() => {
              if (!chosen) return;
              changeMany(modelChanges(object(getAt(state.draft, chosen.base)), [target.base], parts));
              onClose();
              toast("已复制模型设置", "success");
            }}
          >
            复制并替换
          </Button>
        </>
      }
    >
      <div className="stack">
        <Combobox
          label="复制来源"
          value={source}
          placeholder="选择会话"
          options={refs.map((ref) => ({
            value: refKey(ref),
            label: ref.label + " · " + modelName(state.draft, ref.base),
          }))}
          onChange={setSource}
        />
        <ModelPartsPicker value={parts} onChange={setParts} />
      </div>
    </Dialog>
  );
}

/** 会话的模型设置：会话模型、闲时压缩与压缩模型，以及批量复制入口。 */
function ModelTab({ self }: { self: ConversationRef }) {
  const state = useConfig();
  const [applying, setApplying] = useState(false);
  const [copying, setCopying] = useState(false);
  const conversation = object(getAt(state.draft, self.base));
  const model = object(conversation.model);
  const idle = object(conversation.idle_compaction);
  const compactor = object(idle.model);
  const hasProviders = Object.keys(object(state.draft.providers)).length > 0;
  const budget = typeof model.max_context_tokens === "number" ? model.max_context_tokens : 0;
  const target = typeof model.compaction_target_percent === "number" ? model.compaction_target_percent : 0;
  return (
    <div className="stack">
      {!hasProviders ? (
        <Banner
          tone="danger"
          icon={CircleAlert}
          actions={
            <Button size="sm" variant="primary" onClick={() => void navigate("/providers?add=1")}>
              添加服务商
            </Button>
          }
        >
          还没有服务商。先添加服务商，再为这个会话选择模型。
        </Banner>
      ) : (
        !hasSessionModel(state.draft, conversation) && (
          <Banner tone={conversation.enabled === true ? "danger" : "warning"} icon={CircleAlert}>
            还没有选好会话模型，Agent 不会回复这个会话。
          </Banner>
        )
      )}
      {idle.enabled === true && !!compactor.provider && !compactor.name && (
        <Banner tone="warning" icon={CircleAlert}>
          压缩模型还没有填写模型名称，闲时压缩会暂时使用会话模型。
        </Banner>
      )}
      <div className="row wrap model-actions">
        <span className="muted small grow">模型设置可以从其他会话复制，也可以一次应用到多个会话。</span>
        <Button size="sm" icon={Copy} onClick={() => setCopying(true)}>
          从其他会话复制
        </Button>
        <Button size="sm" icon={ListChecks} onClick={() => setApplying(true)}>
          应用到其他会话
        </Button>
      </div>
      <ConfigSection
        def={sessionModelSection}
        base={[...self.base, "model"]}
        helpFor={{
          max_context_tokens: budget
            ? "达到 " +
              formatNumber(Math.floor((budget * 80) / 100)) +
              " token（80%）时压缩旧资料。不要超过模型的总上下文长度；设得更小会更早压缩，每次唤醒发送的内容也更少。"
            : undefined,
          compaction_target_percent:
            budget && target
              ? "达到预算的 80% 时，用会话模型把旧资料压缩到约 " +
                formatNumber(Math.floor((budget * target) / 100)) +
                " token。"
              : undefined,
        }}
      />
      <ConfigSection def={idleCompactionSection} base={[...self.base, "idle_compaction"]} />
      <ConfigSection def={compactionModelSection} base={[...self.base, "idle_compaction", "model"]} />
      <ApplyModelDialog open={applying} onClose={() => setApplying(false)} source={self} />
      <CopyModelDialog open={copying} onClose={() => setCopying(false)} target={self} />
    </div>
  );
}

/** 列表状态点：启用但没有选好模型的会话不会回复，单独标出。 */
function conversationStatus(config: ObjectValue, conversation: ObjectValue) {
  if (conversation.enabled !== true) return { tone: "neutral", label: "未启用" } as const;
  if (!hasSessionModel(config, conversation)) return { tone: "warning", label: "已启用，未选模型" } as const;
  return { tone: "success", label: "已启用" } as const;
}

function ConversationList({ route, onAdd }: { route: ConversationRoute; onAdd: () => void }) {
  const state = useConfig();
  const groups = useGroupNames();
  const [query, setQuery] = useState("");
  const configured = object(state.draft.groups);
  const text = query.trim().toLowerCase();
  const ids = Object.keys(configured).filter(
    (id) => !text || (id + " " + (groups.get(id)?.group_name ?? "")).toLowerCase().includes(text),
  );
  const privateStatus = conversationStatus(state.draft, object(state.draft.private));
  const showPrivate = !text || "私聊".includes(text);
  return (
    <>
      <div className="md-list-head">
        <SearchInput value={query} onChange={setQuery} placeholder="搜索群名或群号" label="搜索会话" />
      </div>
      <ul className="md-items">
        {showPrivate && (
          <li>
            <a
              href="/conversations/private"
              className={"md-item" + (route.kind === "private" ? " active" : "")}
              aria-current={route.kind === "private" ? "page" : undefined}
              onClick={linkHandler("/conversations/private")}
            >
              <User size={16} className="md-item-icon" aria-hidden="true" />
              <span className="md-item-text">
                <span className="md-item-title">私聊</span>
                <span className="md-item-sub">所有私聊共用</span>
              </span>
              <StatusDot tone={privateStatus.tone} label={privateStatus.label} />
            </a>
          </li>
        )}
        {ids.length > 0 && <li className="md-section">群 · {Object.keys(configured).length}</li>}
        {ids.map((id) => {
          const status = conversationStatus(state.draft, object(configured[id]));
          const info = groups.get(id);
          const active = route.kind === "group" && route.id === id;
          const to = conversationRoute("group", id);
          return (
            <li key={id}>
              <a
                href={to}
                className={"md-item" + (active ? " active" : "")}
                aria-current={active ? "page" : undefined}
                onClick={linkHandler(to)}
              >
                <Hash size={16} className="md-item-icon" aria-hidden="true" />
                <span className="md-item-text">
                  <span className="md-item-title">{info?.group_name || id}</span>
                  <span className="md-item-sub mono">{info?.group_name ? id : "群名未知"}</span>
                </span>
                <StatusDot tone={status.tone} label={status.label} />
              </a>
            </li>
          );
        })}
        {!Object.keys(configured).length && !text && (
          <li className="md-empty">
            <p>还没有添加群。</p>
            <Button size="sm" icon={Plus} onClick={onAdd}>
              添加群
            </Button>
          </li>
        )}
        {text && !ids.length && !showPrivate && <li className="md-empty muted">没有匹配的会话</li>}
      </ul>
    </>
  );
}

function ConversationDetail({ route }: { route: ConversationRoute }) {
  const state = useConfig();
  const groups = useGroupNames();
  const [copyOpen, setCopyOpen] = useState(false);
  const kind = route.kind!;
  const base: Path = kind === "private" ? ["private"] : ["groups", route.id];
  const raw = getAt(state.draft, base);
  const back = { to: "/conversations", label: "全部会话" };
  const suggestions = useMemo(
    () =>
      [...groups.values()]
        .filter((group) => !(kind === "group" && group.group_id === route.id))
        .map((group) => ({ value: group.group_id, label: group.group_name || group.group_id })),
    [groups, kind, route.id],
  );
  if (raw === undefined)
    return (
      <EmptyState
        icon={CircleAlert}
        title="这个群不在配置中"
        action={<Button onClick={() => void navigate("/conversations")}>返回会话列表</Button>}
      >
        群 {route.id} 可能已被删除，或者配置文件被外部修改。
      </EmptyState>
    );
  const conversation = object(raw);
  const enabled = conversation.enabled === true;
  const info = kind === "group" ? groups.get(route.id) : undefined;
  const title = kind === "private" ? "私聊" : groupTitle(route.id, groups);
  const label = kind === "private" ? "私聊" : "群 " + route.id;
  const setEnabled = async (next: boolean) => {
    if (
      !next &&
      !(await confirm({
        title: "停用" + (kind === "private" ? "私聊" : "这个群") + "？",
        message: "停用后 Agent 不再响应" + (kind === "private" ? "任何私聊消息" : "这个群的消息") + "，设置会保留。",
        confirmLabel: "停用",
        danger: true,
      }))
    )
      return;
    change([...base, "enabled"], next);
  };
  const self: ConversationRef = {
    base,
    kind,
    id: route.id,
    label: kind === "private" ? "私聊" : title,
    route: conversationRoute(kind, route.id, "model"),
  };
  return (
    <div className="detail">
      <DetailHeader
        back={back}
        title={title}
        subtitle={
          kind === "group" ? (
            <span className="row">
              <span className="mono">{route.id}</span>
              <CopyButton text={route.id} label="复制群号" />
              {info?.member_count != null && (
                <span className="muted">
                  {formatNumber(info.member_count)}
                  {info.max_member_count ? " / " + formatNumber(info.max_member_count) : ""} 人
                </span>
              )}
            </span>
          ) : (
            "所有私聊对象共用这一套设置"
          )
        }
        actions={
          <>
            <label className="switch-label">
              <span>{enabled ? "已启用" : "未启用"}</span>
              <Switch checked={enabled} label={"启用" + label} onChange={(next) => void setEnabled(next)} />
            </label>
            <Menu
              items={[
                { label: "从其他会话复制权限", icon: Copy, onSelect: () => setCopyOpen(true) },
                ...(kind === "group"
                  ? [
                      {
                        label: "删除群",
                        icon: Trash2,
                        danger: true,
                        onSelect: async () => {
                          const accepted = await confirm({
                            title: "删除群 " + route.id + "？",
                            message: "Agent 将不再响应这个群，它的唤醒词、模型设置、能力权限和参数限制会一并删除。",
                            confirmLabel: "删除",
                            danger: true,
                          });
                          if (!accepted) return;
                          change(base, undefined);
                          void navigate("/conversations", { replace: true });
                          toast("已删除群 " + route.id);
                        },
                      },
                    ]
                  : []),
              ]}
            />
          </>
        }
      />
      {!enabled && (
        <Banner
          tone="warning"
          icon={CircleAlert}
          actions={
            <Button size="sm" variant="primary" onClick={() => void setEnabled(true)}>
              启用
            </Button>
          }
        >
          {kind === "private" ? "私聊未启用，Agent 不会回复任何私聊消息。" : "这个群未启用，Agent 不会响应群里的消息。"}
        </Banner>
      )}
      {enabled && !hasSessionModel(state.draft, conversation) && route.tab !== "model" && (
        <Banner
          tone="danger"
          icon={CircleAlert}
          actions={
            <Button
              size="sm"
              variant="primary"
              onClick={() =>
                void navigate(conversationRoute(kind, route.id, "model"), { replace: true, keepScroll: true })
              }
            >
              选择模型
            </Button>
          }
        >
          还没有选好会话模型，Agent 不会回复这个会话。
        </Banner>
      )}
      <Tabs
        label="会话设置"
        value={route.tab}
        onChange={(tab: ConversationTab) =>
          void navigate(conversationRoute(kind, route.id, tab), { replace: true, keepScroll: true })
        }
        items={[
          { id: "general", label: "常规" },
          { id: "model", label: "模型" },
          { id: "permissions", label: "能力权限" },
        ]}
      />
      {route.tab === "model" ? (
        <ModelTab self={self} />
      ) : route.tab === "general" ? (
        <div className="stack">
          {kind === "private" ? (
            <p className="muted small">私聊中的每条消息都会唤醒 Agent，不需要关键词。</p>
          ) : (
            <ConfigSection def={wakeSection} base={base} />
          )}
          <ConfigSection
            def={limitsSection}
            base={[...base, "policy"]}
            suggestions={{ cross_groups: suggestions }}
            describe={{
              cross_groups: (item) => {
                const name = groups.get(String(item))?.group_name;
                return name || undefined;
              },
            }}
          />
        </div>
      ) : (
        <PermissionEditor base={base} kind={kind} />
      )}
      <CopyPolicyDialog open={copyOpen} onClose={() => setCopyOpen(false)} target={base} targetLabel={label} />
    </div>
  );
}

export function ConversationsPage({ route }: { route: ConversationRoute }) {
  const [adding, setAdding] = useState(false);
  const location = useLocation();
  useEffect(() => {
    // 命令面板的“添加群”通过 ?add=1 打开对话框。
    if (new URL(location, window.location.origin).searchParams.get("add") === "1") {
      setAdding(true);
      void navigate("/conversations", { replace: true, keepScroll: true });
    }
  }, [location]);
  return (
    <div className="page">
      <PageHeader
        title="会话"
        description="分别管理私聊和各群的启用开关、唤醒方式、会话模型与能力权限。"
        actions={
          <Button icon={Plus} onClick={() => setAdding(true)}>
            添加群
          </Button>
        }
      />
      <MasterDetail label="会话列表" hasDetail={route.kind !== null} list={<ConversationList route={route} onAdd={() => setAdding(true)} />}>
        {route.kind ? (
          <ConversationDetail key={route.kind + route.id} route={route} />
        ) : (
          <EmptyState icon={MessagesSquare} title="选择一个会话">
            在左侧选择私聊或群，查看唤醒方式、模型设置和能力权限。
          </EmptyState>
        )}
      </MasterDetail>
      <AddGroupDialog open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}
