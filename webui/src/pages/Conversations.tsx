import { useEffect, useMemo, useRef, useState } from "react";
import {
  CircleAlert,
  Copy,
  FileText,
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
import { Banner, Button, EmptyState, IconButton, StatusDot, Switch } from "../components/primitives";
import { CopyButton, DetailHeader, MasterDetail, QQAvatar, SearchInput } from "../components/shell";
import { change, changeMany, useConfig } from "../lib/config";
import { conversationRoute, groupTitle } from "../lib/conversations";
import { formatNumber } from "../lib/format";
import {
  conversationChanges,
  conversationCopyParts,
  conversationRefs,
  hasSessionModel,
} from "../lib/models";
import type { ConversationRef } from "../lib/models";
import { linkHandler, navigate, prefersReducedMotion, useLocation } from "../lib/navigation";
import {
  filesResource,
  groupsResource,
  reloadGroups,
  useGroupNames,
  useResource,
} from "../lib/resource";
import { getAt, object } from "../lib/types";
import type { ObjectValue, Path } from "../lib/types";
import { confirm, toast } from "../lib/ui";
import type { Route } from "../routes";
import {
  compactionModelSection,
  conversationEntries,
  defaultConversation,
  idleCompactionSection,
  limitsSection,
  promptsSection,
  sessionModelSection,
  wakeSection,
} from "../schema/fields";

type ConversationRoute = Extract<Route, { page: "conversations" }>;
const groupIdPattern = /^\d{5,15}$/;
const allCopyParts = () => conversationCopyParts.map((part) => part.id);
const refKey = (ref: ConversationRef) => ref.base.join(":");

/** 会话当前选择的模型名称，用于列表和选择器中的说明。 */
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
  const [templateSource, setTemplateSource] = useState("");
  const sources = conversationRefs(state.draft, groups);
  const readySources = sources.filter((ref) =>
    hasSessionModel(state.draft, object(getAt(state.draft, ref.base))),
  );
  useEffect(() => {
    if (open) {
      setId("");
      setQuery("");
      setEnable(true);
      // 默认沿用第一个已选好模型的会话，免得新群加进来需要逐项重填。
      const preferred = readySources[0] ?? sources[0];
      setTemplateSource(preferred ? refKey(preferred) : "");
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
    const source = sources.find((ref) => refKey(ref) === templateSource);
    const base: Path = ["groups", groupId];
    changeMany([
      [base, { ...defaultConversation(), enabled: enable }],
      ...(source
        ? conversationChanges(
            object(getAt(state.draft, source.base)),
            source.base,
            [base],
            allCopyParts(),
          )
        : []),
    ]);
    onClose();
    void navigate(conversationRoute("group", groupId));
    toast("已添加群 " + groupId + (enable ? "，已启用" : ""), "success");
  };
  return (
    <Dialog
      open={open}
      title="添加群"
      description="可一键复制已有会话的全部设置，也可在添加后单独调整。"
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
          <input
            type="checkbox"
            checked={enable}
            onChange={(event) => setEnable(event.target.checked)}
          />
          添加后立即启用
        </label>
        <div className="field-stacked">
          <span className="field-label">复制已有会话配置</span>
          <Combobox
            label="复制已有会话配置"
            value={templateSource}
            onChange={setTemplateSource}
            options={[
              { value: "", label: "不复制，使用默认初始配置" },
              ...sources.map((ref) => ({
                value: refKey(ref),
                label: "复制自 " + ref.label + " · " + modelName(state.draft, ref.base),
              })),
            ]}
          />
          <span className="field-help">
            一键复制唤醒与限制、提示词与知识库、会话模型、闲时压缩与能力权限；添加后仍可单独修改。
          </span>
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
              <SearchInput
                value={query}
                onChange={setQuery}
                placeholder="按群名或群号筛选"
                label="筛选群"
                pageSearch={false}
              />
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

function ConversationPartsPicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const allChecked = value.length === conversationCopyParts.length;
  return (
    <fieldset className="model-parts">
      <legend className="row">
        <span className="field-label grow">包含的配置模块</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onChange(allChecked ? [] : allCopyParts())}
        >
          {allChecked ? "取消全选" : "全选"}
        </Button>
      </legend>
      {conversationCopyParts.map((part) => (
        <label className="check-row" key={part.id} title={part.description}>
          <input
            type="checkbox"
            checked={value.includes(part.id)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...value, part.id]
                  : value.filter((item) => item !== part.id),
              )
            }
          />
          <span>
            <span>{part.label}</span>
            <span className="muted small"> · {part.description}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

/** 把当前会话所选模块的配置一次应用到多个会话。 */
function ApplyConversationDialog({
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
  const [parts, setParts] = useState<string[]>(allCopyParts);
  const [targets, setTargets] = useState<Set<string>>(() => new Set());
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (open) {
      setParts(allCopyParts());
      setTargets(new Set());
      setQuery("");
    }
  }, [open]);
  const refs = conversationRefs(state.draft, groups).filter(
    (ref) => refKey(ref) !== refKey(source),
  );
  const text = query.trim().toLowerCase();
  const visible = refs.filter(
    (ref) => !text || (ref.label + " " + ref.id).toLowerCase().includes(text),
  );
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
      conversationChanges(
        object(getAt(state.draft, source.base)),
        source.base,
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
      description={
        "把「" + source.label + "」勾选的配置模块一键应用到所选会话，覆盖它们对应的配置。"
      }
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
        <ConversationPartsPicker value={parts} onChange={setParts} />
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
            <SearchInput
              value={query}
              onChange={setQuery}
              placeholder="按群名或群号筛选"
              label="筛选会话"
              pageSearch={false}
            />
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
                    <span className="muted mono small truncate">
                      {modelName(state.draft, ref.base)}
                    </span>
                  </label>
                </li>
              ))
            ) : (
              <li className="muted small picker-empty">
                {text ? "没有匹配的会话" : "没有其他会话"}
              </li>
            )}
          </ul>
        </section>
      </div>
    </Dialog>
  );
}

/** 从其他会话复制所选模块的配置到当前会话。 */
function CopyConversationDialog({
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
  const [parts, setParts] = useState<string[]>(allCopyParts);
  useEffect(() => {
    if (open) {
      setSource("");
      setParts(allCopyParts());
    }
  }, [open]);
  const refs = conversationRefs(state.draft, groups).filter(
    (ref) => refKey(ref) !== refKey(target),
  );
  const chosen = refs.find((ref) => refKey(ref) === source);
  return (
    <Dialog
      open={open}
      title="从其他会话复制"
      description={"用所选会话的配置替换「" + target.label + "」勾选的模块。"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={!chosen || !parts.length}
            onClick={() => {
              if (!chosen) return;
              changeMany(
                conversationChanges(
                  object(getAt(state.draft, chosen.base)),
                  chosen.base,
                  [target.base],
                  parts,
                ),
              );
              onClose();
              toast("已从「" + chosen.label + "」复制所选配置", "success");
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
        <ConversationPartsPicker value={parts} onChange={setParts} />
      </div>
    </Dialog>
  );
}

/** 列表状态点：启用但没有选好模型的会话不会回复，单独标出。 */
function conversationStatus(config: ObjectValue, conversation: ObjectValue) {
  if (conversation.enabled !== true) return { tone: "neutral", label: "未启用" } as const;
  if (!hasSessionModel(config, conversation))
    return { tone: "warning", label: "已启用，未选模型" } as const;
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
        <div className="md-list-search-row">
          <SearchInput value={query} onChange={setQuery} placeholder="搜索群名或群号" label="搜索会话" />
          <IconButton icon={Plus} label="添加群" className="md-add-btn" onClick={onAdd} />
        </div>
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
              <QQAvatar kind="icon" icon={User} name="私聊" size="lg" />
              <span className="md-item-text">
                <span className="qq-item-top">
                  <span className="md-item-title">私聊</span>
                  <StatusDot tone={privateStatus.tone} label={privateStatus.label} />
                </span>
                <span className="qq-item-bottom">
                  <span className="md-item-sub">所有私聊共用 · {modelName(state.draft, ["private"])}</span>
                </span>
              </span>
            </a>
          </li>
        )}
        {ids.length > 0 && <li className="md-section">群 · {Object.keys(configured).length}</li>}
        {ids.map((id) => {
          const status = conversationStatus(state.draft, object(configured[id]));
          const info = groups.get(id);
          const active = route.kind === "group" && route.id === id;
          const to = conversationRoute("group", id);
          const title = info?.group_name || id;
          return (
            <li key={id}>
              <a
                href={to}
                className={"md-item" + (active ? " active" : "")}
                aria-current={active ? "page" : undefined}
                onClick={linkHandler(to)}
              >
                <QQAvatar kind="group" id={id} name={title} size="lg" />
                <span className="md-item-text">
                  <span className="qq-item-top">
                    <span className="md-item-title">{title}</span>
                    <StatusDot tone={status.tone} label={status.label} />
                  </span>
                  <span className="qq-item-bottom">
                    <span className="md-item-sub">
                      <span className="mono">{id}</span> · {modelName(state.draft, ["groups", id])}
                    </span>
                  </span>
                </span>
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
  const header = useRef<HTMLDivElement>(null);
  const state = useConfig();
  const groups = useGroupNames();
  const files = useResource(filesResource, 30_000);
  const [copying, setCopying] = useState(false);
  const [applying, setApplying] = useState(false);
  const kind = route.kind!;
  const base: Path = kind === "private" ? ["private"] : ["groups", route.id];
  const raw = getAt(state.draft, base);
  const back = { to: "/conversations", label: "全部会话" };
  const groupSuggestions = useMemo(
    () =>
      [...groups.values()]
        .filter((group) => !(kind === "group" && group.group_id === route.id))
        .map((group) => ({ value: group.group_id, label: group.group_name || group.group_id })),
    [groups, kind, route.id],
  );
  const fileOptions = useMemo(() => {
    const names = new Set<string>();
    for (const file of files.data ?? []) {
      if (/\.(md|txt)$/i.test(file)) names.add(file);
    }
    for (const [, conv] of conversationEntries(state.draft)) {
      if (typeof conv.persona_file === "string" && conv.persona_file.trim())
        names.add(conv.persona_file.trim());
      if (Array.isArray(conv.knowledge_files))
        for (const item of conv.knowledge_files)
          if (typeof item === "string" && item.trim()) names.add(item.trim());
    }
    return [...names]
      .sort((a, b) => a.localeCompare(b))
      .map((path) => ({ value: path, label: path }));
  }, [files.data, state.draft]);

  if (raw === undefined)
    return (
      <div className="detail">
        <div className="detail-sticky">
          <DetailHeader back={back} title={route.id} save />
        </div>
        <div className="detail-body">
          <EmptyState
            icon={CircleAlert}
            title="这个群不在配置中"
            action={<Button onClick={() => void navigate("/conversations")}>返回会话列表</Button>}
          >
            群 {route.id} 可能已被删除，或者配置文件被外部修改。
          </EmptyState>
        </div>
      </div>
    );
  const conversation = object(raw);
  const enabled = conversation.enabled === true;
  const info = kind === "group" ? groups.get(route.id) : undefined;
  const title = kind === "private" ? "私聊" : groupTitle(route.id, groups);
  const label = kind === "private" ? "私聊" : "群 " + route.id;
  const model = object(conversation.model);
  const idle = object(conversation.idle_compaction);
  const compactor = object(idle.model);
  const hasProviders = Object.keys(object(state.draft.providers)).length > 0;
  const budget = typeof model.max_context_tokens === "number" ? model.max_context_tokens : 0;
  const target =
    typeof model.compaction_target_percent === "number" ? model.compaction_target_percent : 0;
  const personaFile =
    typeof conversation.persona_file === "string" && conversation.persona_file.trim()
      ? conversation.persona_file.trim()
      : "persona.md";

  const setEnabled = async (next: boolean) => {
    if (
      !next &&
      !(await confirm({
        title: "停用" + (kind === "private" ? "私聊" : "这个群") + "？",
        message:
          "停用后 Agent 不再响应" +
          (kind === "private" ? "任何私聊消息" : "这个群的消息") +
          "，设置会保留。",
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
    route: conversationRoute(kind, route.id),
  };
  const hasOtherConversations = kind === "group" || Object.keys(object(state.draft.groups)).length > 0;
  const jumpTo = (sectionId: string) => {
    const section = document.getElementById(sectionId);
    if (!section || !header.current) return;
    const offset = header.current.offsetHeight + parseFloat(getComputedStyle(header.current).top) + 12;
    section.style.scrollMarginTop = offset + "px";
    section.scrollIntoView({ behavior: prefersReducedMotion() ? "instant" : "smooth", block: "start" });
  };
  const anchors = [
    ...(kind === "group" ? [{ id: "section-wake", label: "唤醒" }] : []),
    { id: "section-limits", label: "限制" },
    { id: "section-prompts", label: "提示词" },
    { id: "section-session-model", label: "模型" },
    { id: "section-idle-compaction", label: "闲时压缩" },
    { id: "section-permissions", label: "能力权限" },
  ];

  return (
    <div className="detail">
      <div className="detail-sticky" ref={header}>
        <DetailHeader
          back={back}
          avatar={
            <QQAvatar
              kind={kind === "private" ? "icon" : "group"}
              id={route.id}
              icon={kind === "private" ? User : undefined}
              name={title}
              size="md"
            />
          }
          title={title}
          save
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
              <Button
                size="sm"
                icon={Copy}
                disabled={!hasOtherConversations}
                onClick={() => setCopying(true)}
              >
                从其他会话复制
              </Button>
              <Button
                size="sm"
                icon={ListChecks}
                disabled={!hasOtherConversations}
                onClick={() => setApplying(true)}
              >
                应用到其他会话
              </Button>
              <label className="switch-label">
                <span>{enabled ? "已启用" : "未启用"}</span>
                <Switch
                  checked={enabled}
                  label={"启用" + label}
                  onChange={(next) => void setEnabled(next)}
                />
              </label>
              {kind === "group" && (
                <Menu
                  items={[
                    {
                      label: "删除群",
                      icon: Trash2,
                      danger: true,
                      onSelect: async () => {
                        const accepted = await confirm({
                          title: "删除群 " + route.id + "？",
                          message:
                            "Agent 将不再响应这个群，它的唤醒词、提示词、模型设置、能力权限和参数限制会一并删除。",
                          confirmLabel: "删除",
                          danger: true,
                        });
                        if (!accepted) return;
                        change(base, undefined);
                        void navigate("/conversations", { replace: true });
                        toast("已删除群 " + route.id);
                      },
                    },
                  ]}
                />
              )}
            </>
          }
        />
        <nav className="detail-anchors" aria-label="会话设置分组跳转">
          {anchors.map((anchor) => (
            <button
              key={anchor.id}
              type="button"
              className="detail-anchor-pill"
              onClick={() => jumpTo(anchor.id)}
            >
              {anchor.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="detail-body stack">
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
            {kind === "private"
              ? "私聊未启用，Agent 不会回复任何私聊消息。"
              : "这个群未启用，Agent 不会响应群里的消息。"}
          </Banner>
        )}
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
            <Banner tone={enabled ? "danger" : "warning"} icon={CircleAlert}>
              还没有选好会话模型，Agent 不会回复这个会话。
            </Banner>
          )
        )}
        {idle.enabled === true && !!compactor.provider && !compactor.name && (
          <Banner tone="warning" icon={CircleAlert}>
            压缩模型还没有填写模型名称，闲时压缩会暂时使用会话模型。
          </Banner>
        )}

        {kind === "private" ? (
          <p className="muted small">私聊中的每条消息都会唤醒 Agent，不需要唤醒关键词。</p>
        ) : (
          <ConfigSection def={wakeSection} base={base} />
        )}

        <ConfigSection
          def={limitsSection}
          base={[...base, "policy"]}
          suggestions={{ cross_groups: groupSuggestions }}
          describe={{
            cross_groups: (item) => {
              const name = groups.get(String(item))?.group_name;
              return name || undefined;
            },
          }}
        />

        <ConfigSection
          def={promptsSection}
          base={base}
          suggestions={{
            persona_file: fileOptions,
            knowledge_files: fileOptions,
          }}
          actions={
            <Button
              size="sm"
              variant="ghost"
              icon={FileText}
              onClick={() => void navigate("/prompts?path=" + encodeURIComponent(personaFile))}
            >
              编辑提示词文件
            </Button>
          }
        />

        <ConfigSection
          def={sessionModelSection}
          base={[...base, "model"]}
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
        <ConfigSection def={idleCompactionSection} base={[...base, "idle_compaction"]} />
        <ConfigSection def={compactionModelSection} base={[...base, "idle_compaction", "model"]} />

        <div id="section-permissions">
          <PermissionEditor base={base} kind={kind} />
        </div>
      </div>

      <ApplyConversationDialog open={applying} onClose={() => setApplying(false)} source={self} />
      <CopyConversationDialog open={copying} onClose={() => setCopying(false)} target={self} />
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
    <>
      <MasterDetail
        label="会话列表"
        hasDetail={route.kind !== null}
        list={<ConversationList route={route} onAdd={() => setAdding(true)} />}
      >
        {route.kind ? (
          <ConversationDetail key={route.kind + route.id} route={route} />
        ) : (
          <div className="detail">
            <div className="detail-sticky">
              <DetailHeader
                title="会话"
                save
                subtitle="在一个视图中集中配置私聊和各群的唤醒限制、提示词与知识库、模型与压缩、能力权限，支持跨会话一键复制与批量应用。"
                actions={
                  <Button icon={Plus} onClick={() => setAdding(true)}>
                    添加群
                  </Button>
                }
              />
            </div>
            <div className="detail-body">
              <EmptyState icon={MessagesSquare} title="选择一个会话">
                在左侧选择私聊或群，在同一页面集中配置唤醒、提示词、模型和能力权限。
              </EmptyState>
            </div>
          </div>
        )}
      </MasterDetail>
      <AddGroupDialog open={adding} onClose={() => setAdding(false)} />
    </>
  );
}
