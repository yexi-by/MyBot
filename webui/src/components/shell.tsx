import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowLeft,
  Box,
  Check,
  CircleAlert,
  Copy,
  LoaderCircle,
  PencilLine,
  PowerOff,
  RotateCw,
  Search,
  TriangleAlert,
  WifiOff,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  changedPaths,
  discardConflict,
  overwriteConflict,
  save,
  useConfig,
} from "../lib/config";
import { errorMessage } from "../lib/api";
import { formatClock } from "../lib/format";
import { restartSections } from "../lib/labels";
import { navigate, useLocation } from "../lib/navigation";
import { dismissPower, restartHarness, usePower } from "../lib/power";
import { setConflictDialog, toast, useUi } from "../lib/ui";
import { focusField } from "../routes";
import { fieldLabel } from "../schema/fields";
import { Dialog } from "./Dialog";
import { Banner, Button, IconButton } from "./primitives";

/** 复制到剪贴板；局域网 HTTP 页面没有 Clipboard API 时退回到选区复制。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 退回到下面的方式 */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  (document.querySelector("dialog[open]") ?? document.body).appendChild(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  return copied;
}

export function CopyButton({ text, label, size = "sm" }: { text: string; label: string; size?: "sm" | "md" }) {
  return (
    <IconButton
      icon={Copy}
      size={size}
      label={label}
      onClick={async () => {
        if (await copyText(text)) toast("已复制", "success");
        else toast("复制失败，请手动选择文本复制", "danger");
      }}
    />
  );
}

/** 点击触发的浮层，Esc 或点击外部关闭。 */
export function Popover({
  trigger,
  children,
  open,
  onOpenChange,
  label,
}: {
  trigger: ReactNode;
  children: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) onOpenChange(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onOpenChange(false);
        root.current?.querySelector<HTMLElement>("button")?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", key, true);
    };
  }, [open, onOpenChange]);
  return (
    <div className="popover-anchor" ref={root}>
      {trigger}
      {open && (
        <div className="popover" role="dialog" aria-label={label}>
          {children}
        </div>
      )}
    </div>
  );
}

function statusOf(state: ReturnType<typeof useConfig>): {
  tone: string;
  icon: LucideIcon;
  text: string;
  spin?: boolean;
} {
  if (state.conflict) return { tone: "warning", icon: TriangleAlert, text: "外部已修改" };
  if (state.issues.length)
    return { tone: "danger", icon: CircleAlert, text: state.issues.length + " 处需要修正" };
  if (state.offline) return { tone: "warning", icon: WifiOff, text: "连接中断" };
  if (state.saving) return { tone: "neutral", icon: LoaderCircle, text: "保存中…", spin: true };
  if (state.error) return { tone: "danger", icon: CircleAlert, text: "保存失败" };
  if (state.changed.length)
    return { tone: "neutral", icon: PencilLine, text: "未保存 " + state.changed.length + " 项" };
  return { tone: "success", icon: Check, text: "已保存" };
}

/** 页头的保存状态；点开查看待修正字段、未保存内容和最近保存记录。 */
export function SaveStatus() {
  const state = useConfig();
  const [open, setOpen] = useState(false);
  if (!state.loaded) return null;
  const status = statusOf(state);
  const Icon = status.icon;
  const unsaved = [...new Set(state.changed.map((path) => fieldLabel(state.draft, path)))];
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      label="保存状态"
      trigger={
        <button
          type="button"
          className={"save-status save-" + status.tone}
          aria-expanded={state.conflict ? undefined : open}
          onClick={() => (state.conflict ? setConflictDialog(true) : setOpen(!open))}
        >
          <Icon size={15} className={status.spin ? "spin" : undefined} aria-hidden="true" />
          <span>{status.text}</span>
        </button>
      }
    >
      <div className="save-panel">
        {state.issues.length > 0 && (
          <section>
            <h3>需要修正后才能保存</h3>
            <ul className="save-list">
              {state.issues.map((issue, index) => (
                <li key={index}>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => {
                      setOpen(false);
                      void focusField(issue.path);
                    }}
                  >
                    {fieldLabel(state.draft, issue.path)}
                  </button>
                  <span className="danger-text">{issue.message}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {state.error && !state.issues.length && (
          <section>
            <h3>{state.offline ? "连接中断" : "保存没有完成"}</h3>
            <p className="secondary">{state.error}</p>
            {!state.offline && (
              <Button size="sm" onClick={() => void save()}>
                重试
              </Button>
            )}
          </section>
        )}
        {unsaved.length > 0 && !state.issues.length && (
          <section>
            <h3>尚未保存</h3>
            <p className="secondary">{unsaved.join("、")}</p>
            {!state.saving && !state.offline && (
              <Button size="sm" onClick={() => void save()}>
                立即保存
              </Button>
            )}
          </section>
        )}
        <section>
          <h3>最近保存</h3>
          {state.history.length ? (
            <ul className="save-history">
              {state.history.map((record) => (
                <li key={record.at}>
                  <span className="muted num">{formatClock(record.at / 1000)}</span>
                  <span>{record.fields.length ? record.fields.join("、") : record.title}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">本次打开控制台后还没有保存过修改。修改会在停止输入约 1 秒后自动保存。</p>
          )}
        </section>
      </div>
    </Popover>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  save: showSave = true,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  save?: boolean;
}) {
  return (
    <header className="page-header">
      <div className="page-heading">
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      <div className="page-actions">
        {showSave && <SaveStatus />}
        {actions && <div className="page-extra">{actions}</div>}
      </div>
    </header>
  );
}

/** 主从视图中详情区的标题行；窄屏时带返回列表的按钮。 */
export function DetailHeader({
  title,
  subtitle,
  badges,
  actions,
  back,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  badges?: ReactNode;
  actions?: ReactNode;
  back: { to: string; label: string };
}) {
  return (
    <header className="detail-header">
      <button type="button" className="md-back" onClick={() => void navigate(back.to)}>
        <ArrowLeft size={16} aria-hidden="true" />
        {back.label}
      </button>
      <div className="detail-heading">
        <div className="detail-title">
          <h2>{title}</h2>
          {badges}
        </div>
        {subtitle && <div className="detail-subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="detail-actions">{actions}</div>}
    </header>
  );
}

export function MasterDetail({
  list,
  children,
  hasDetail,
  label,
}: {
  list: ReactNode;
  children: ReactNode;
  hasDetail: boolean;
  label: string;
}) {
  return (
    <div className={"master-detail" + (hasDetail ? " has-detail" : "")}>
      <nav className="md-list" aria-label={label}>
        {list}
      </nav>
      <div className="md-detail">{children}</div>
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  label,
  pageSearch = true,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
  pageSearch?: boolean;
}) {
  return (
    <label className="search-input">
      <Search size={15} aria-hidden="true" />
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={label}
        data-page-search={pageSearch || undefined}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            event.stopPropagation();
            onChange("");
          }
        }}
      />
    </label>
  );
}

export function GlobalBanners() {
  const state = useConfig();
  const location = useLocation();
  const meta = state.meta;
  // 配置文件页自身会说明错误，不重复显示。
  const repairing = location.startsWith("/settings/config-file");
  const sections = meta.restart_sections.filter(
    (name) => !(name === "sandbox" && meta.sandbox_restart_required),
  );
  return (
    <div className="global-banners">
      {state.offline ? (
        <Banner tone="danger" icon={WifiOff} title="无法连接控制台服务">
          正在自动重试；恢复后会继续保存未保存的修改。
        </Banner>
      ) : state.repairPath && !repairing ? (
        <Banner
          tone="danger"
          icon={CircleAlert}
          title="配置文件无效"
          actions={
            <Button size="sm" onClick={() => void navigate("/settings/config-file")}>
              修复配置文件
            </Button>
          }
        >
          {state.error || "配置文件不符合要求"}
        </Banner>
      ) : null}
      {state.loaded && sections.length > 0 && (
        <Banner
          tone="warning"
          icon={RotateCw}
          title="部分修改需要重启后生效"
          actions={
            <Button size="sm" onClick={() => void restartHarness()}>
              重启 Harness
            </Button>
          }
        >
          {sections.map((name) => restartSections[name] ?? name).join("、")}
        </Banner>
      )}
      {state.loaded && meta.sandbox_restart_required && (
        <Banner
          tone="warning"
          icon={Box}
          title="沙箱设置尚未生效"
          actions={
            <Button size="sm" onClick={() => void navigate("/sandbox")}>
              查看沙箱
            </Button>
          }
        >
          请重新创建 Runner 容器，然后重启 Harness。
        </Banner>
      )}
    </div>
  );
}

function summarize(labels: string[]) {
  const unique = [...new Set(labels)];
  return unique.length > 8 ? unique.slice(0, 8).join("、") + " 等 " + unique.length + " 项" : unique.join("、");
}

/** 配置冲突的三个出口：覆盖、丢弃、继续编辑。 */
export function ConflictDialog() {
  const state = useConfig();
  const ui = useUi();
  const [busy, setBusy] = useState(false);
  const latest = state.conflict;
  const close = () => setConflictDialog(false);
  const local = summarize(state.changed.map((path) => fieldLabel(state.draft, path)));
  const external = latest
    ? summarize(changedPaths(state.base, latest.config).map((path) => fieldLabel(latest.config, path)))
    : "";
  return (
    <Dialog
      open={ui.conflict && !!latest}
      title="配置文件已被外部修改"
      description="编辑期间配置文件被其他程序或手动修改，自动保存已暂停。"
      onClose={close}
      footer={
        <>
          <Button onClick={close} data-autofocus>
            继续编辑
          </Button>
          <Button
            variant="danger-ghost"
            disabled={busy}
            onClick={() => {
              discardConflict();
              close();
              toast("已加载最新配置，你的修改已放弃");
            }}
          >
            放弃我的修改
          </Button>
          <Button
            variant="primary"
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                if (await overwriteConflict()) toast("已保存你的修改", "success");
                close();
              } catch (error) {
                toast("无法读取最新配置：" + errorMessage(error), "danger");
              } finally {
                setBusy(false);
              }
            }}
          >
            用我的修改覆盖
          </Button>
        </>
      }
    >
      <dl className="conflict-summary">
        <dt>你尚未保存的修改</dt>
        <dd>{local || "无"}</dd>
        <dt>外部修改的内容</dt>
        <dd>{external || "无法识别具体字段（可能只改动了注释或格式）"}</dd>
      </dl>
      <p className="muted small">
        覆盖会以最新文件为基础写入你改过的字段，其余外部修改保留；同一字段以你的修改为准。选择继续编辑会暂停自动保存，之后可从页头的保存状态再次处理。
      </p>
    </Dialog>
  );
}

export function PowerOverlay() {
  const power = usePower();
  if (power.phase === "idle") return null;
  return (
    <div className="power-overlay" role="alertdialog" aria-modal="true" aria-live="assertive">
      <div className="power-card">
        {power.phase === "restarting" ? (
          <>
            <LoaderCircle className="spin" size={28} aria-hidden="true" />
            <h2>正在重启 Harness…</h2>
            <p>服务恢复后会自动重新加载数据。</p>
          </>
        ) : power.phase === "stopped" ? (
          <>
            <PowerOff size={28} aria-hidden="true" />
            <h2>Harness 已关闭</h2>
            <p>需要在服务器上重新启动容器后，才能继续使用控制台。</p>
          </>
        ) : (
          <>
            <CircleAlert size={28} className="danger-text" aria-hidden="true" />
            <h2>重启没有完成</h2>
            <p>{power.message}</p>
            <div className="row">
              <Button onClick={dismissPower}>关闭提示</Button>
              <Button variant="primary" onClick={() => window.location.reload()}>
                重新加载页面
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
