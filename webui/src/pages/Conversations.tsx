import { useEffect, useMemo, useState } from "react";
import { CircleAlert, Copy, Hash, MessagesSquare, Plus, RefreshCw, Trash2, User } from "lucide-react";
import { Combobox } from "../components/Combobox";
import { Dialog } from "../components/Dialog";
import { ConfigSection } from "../components/form";
import { Menu } from "../components/Menu";
import { PermissionEditor } from "../components/PermissionEditor";
import { Banner, Button, EmptyState, StatusDot, Switch, Tabs } from "../components/primitives";
import { CopyButton, DetailHeader, MasterDetail, PageHeader, SearchInput } from "../components/shell";
import { change, useConfig } from "../lib/config";
import { conversationRoute, groupTitle } from "../lib/conversations";
import { formatNumber } from "../lib/format";
import { linkHandler, navigate, useLocation } from "../lib/navigation";
import { groupsResource, reloadGroups, useGroupNames, useResource } from "../lib/resource";
import { getAt, object } from "../lib/types";
import type { Json, ObjectValue, Path } from "../lib/types";
import { confirm, toast } from "../lib/ui";
import type { Route } from "../routes";
import { defaultConversation, limitsSection, wakeSection } from "../schema/fields";

type ConversationRoute = Extract<Route, { page: "conversations" }>;
const groupIdPattern = /^\d{5,15}$/;

function AddGroupDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const state = useConfig();
  const directory = useResource(groupsResource, open ? 60_000 : 0);
  const [id, setId] = useState("");
  const [enable, setEnable] = useState(true);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (open) {
      setId("");
      setQuery("");
      setEnable(true);
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
    change(["groups", groupId], { ...defaultConversation(), enabled: enable });
    onClose();
    void navigate(conversationRoute("group", groupId));
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

function ConversationList({ route, onAdd }: { route: ConversationRoute; onAdd: () => void }) {
  const state = useConfig();
  const groups = useGroupNames();
  const [query, setQuery] = useState("");
  const configured = object(state.draft.groups);
  const text = query.trim().toLowerCase();
  const ids = Object.keys(configured).filter(
    (id) => !text || (id + " " + (groups.get(id)?.group_name ?? "")).toLowerCase().includes(text),
  );
  const privateOn = object(state.draft.private).enabled === true;
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
              <StatusDot tone={privateOn ? "success" : "neutral"} label={privateOn ? "已启用" : "未启用"} />
            </a>
          </li>
        )}
        {ids.length > 0 && <li className="md-section">群 · {Object.keys(configured).length}</li>}
        {ids.map((id) => {
          const on = object(configured[id]).enabled === true;
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
                <StatusDot tone={on ? "success" : "neutral"} label={on ? "已启用" : "未启用"} />
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
  const tabRoute = (tab: string) =>
    conversationRoute(kind, route.id, tab === "permissions");
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
                            message: "Agent 将不再响应这个群，它的唤醒词、能力权限和参数限制会一并删除。",
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
      <Tabs
        label="会话设置"
        value={route.tab}
        onChange={(tab) => void navigate(tabRoute(tab), { replace: true, keepScroll: true })}
        items={[
          { id: "general", label: "常规" },
          { id: "permissions", label: "能力权限" },
        ]}
      />
      {route.tab === "general" ? (
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
            在左侧选择私聊或群，查看唤醒方式和能力权限。
          </EmptyState>
        )}
      </MasterDetail>
      <AddGroupDialog open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}
