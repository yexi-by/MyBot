import { CircleAlert, Power, RotateCw } from "lucide-react";
import { FileEditor } from "../components/FileEditor";
import { ConfigSection } from "../components/form";
import { Banner, Button, EmptyState, Segmented, StatusDot } from "../components/primitives";
import { CopyButton, PageHeader } from "../components/shell";
import { useConfig } from "../lib/config";
import { formatDuration } from "../lib/format";
import { linkHandler } from "../lib/navigation";
import { restartHarness, shutdownHarness } from "../lib/power";
import { runtimeResource, useResource } from "../lib/resource";
import { setTheme, themeLabels, useTheme } from "../lib/theme";
import type { Theme } from "../lib/theme";
import { object } from "../lib/types";
import { settingsSections } from "../routes";
import type { SettingsSection } from "../routes";
import { napcatSection, runtimeSection, serverSection } from "../schema/fields";
import { napcatAddress } from "./Overview";

function NapcatSettings() {
  const state = useConfig();
  const runtime = useResource(runtimeResource, 5000);
  const bots = runtime.data?.napcat.connected ?? [];
  const token = String(object(state.draft.napcat).token || "");
  const address = napcatAddress(object(state.draft.server).port);
  return (
    <div className="stack">
      <section className="card">
        <header className="card-header">
          <h2 className="grow">连接地址</h2>
          <span className="row small">
            <StatusDot tone={bots.length ? "success" : "warning"} />
            {bots.length ? "已连接：" + bots.join("、") : "未连接"}
          </span>
        </header>
        <div className="card-body stack">
          <p className="secondary small">
            在 NapCat 的网络配置中新建“WebSocket 客户端”（反向 WebSocket），填写下面的地址，并把 Token 设为与本页“连接密钥”相同的值。
          </p>
          <div className="code-line">
            <code>{address}</code>
            <CopyButton text={address} label="复制连接地址" />
          </div>
          {!token && (
            <Banner tone="danger" icon={CircleAlert}>
              还没有设置连接密钥，NapCat 无法接入。可以点击密钥框右侧的按钮生成一个。
            </Banner>
          )}
        </div>
      </section>
      <ConfigSection def={napcatSection} base={["napcat"]} />
    </div>
  );
}

function ConfigFile() {
  const state = useConfig();
  const path = state.meta.config_file || state.repairPath;
  if (!path)
    return (
      <EmptyState icon={CircleAlert} title="无法确定配置文件">
        控制台服务暂时没有响应，恢复连接后再试。
      </EmptyState>
    );
  return (
    <div className="stack">
      {state.repairPath ? (
        <Banner tone="danger" icon={CircleAlert} title="配置文件无效">
          {state.error || "配置文件不符合要求"}。修复并保存后会自动重新加载，其他页面随之恢复。
        </Banner>
      ) : (
        <p className="muted small">
          这里直接编辑配置文件原文。各设置页面的修改也保存在这个文件中，保留注释和格式。
        </p>
      )}
      <FileEditor path={path} kind="toml" note="保存后立即重新加载；需要重启的项会在页面顶部提示。" />
    </div>
  );
}

function Appearance() {
  const theme = useTheme();
  return (
    <section className="card form-section">
      <div className="form-grid">
        <div className="field">
          <div className="field-text">
            <span className="field-label">主题</span>
            <p className="field-help">只保存在当前浏览器中。</p>
          </div>
          <div className="field-control">
            <Segmented<Theme>
              label="主题"
              value={theme}
              onChange={setTheme}
              options={(["system", "light", "dark"] as Theme[]).map((value) => ({
                value,
                label: themeLabels[value],
              }))}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function Service() {
  const state = useConfig();
  const runtime = useResource(runtimeResource, 5000);
  const data = runtime.data;
  const rows: [string, string, boolean][] = [
    ["已运行", data ? formatDuration(Date.now() / 1000 - data.started_at) : "—", false],
    [
      "启动时间",
      data ? new Date(data.started_at * 1000).toLocaleString("zh-CN", { hour12: false }) : "—",
      false,
    ],
    ["启动标识", state.meta.boot_id || "—", true],
    ["配置修订号", state.meta.revision ? String(state.meta.revision) : "—", false],
    ["NapCat 适配版本", data?.napcat.baseline_version || "—", true],
  ];
  return (
    <div className="stack">
      <section className="card">
        <header className="card-header">
          <h2>运行信息</h2>
        </header>
        <dl className="info-list">
          {rows.map(([label, value, mono]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className={mono ? "mono" : "num"}>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="card">
        <header className="card-header">
          <h2>服务控制</h2>
        </header>
        <div className="card-body stack">
          <div className="action-row">
            <div className="grow">
              <h3>重启 Harness</h3>
              <p className="muted small">让 NapCat 接入、MCP 服务等需要重启的修改生效。正在进行的对话会被中断。</p>
            </div>
            <Button icon={RotateCw} onClick={() => void restartHarness()}>
              重启
            </Button>
          </div>
          <div className="action-row">
            <div className="grow">
              <h3>关闭 Harness</h3>
              <p className="muted small">停止服务进程。关闭后需要在服务器上重新启动容器。</p>
            </div>
            <Button variant="danger-ghost" icon={Power} onClick={() => void shutdownHarness()}>
              关闭
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}

export function SettingsPage({ section }: { section: SettingsSection }) {
  const editable = section === "napcat" || section === "runtime" || section === "server";
  return (
    <div className="page">
      <PageHeader title="设置" save={editable} />
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置分类">
          {settingsSections.map((item) => {
            const to = "/settings/" + item.id;
            return (
              <a
                key={item.id}
                href={to}
                className={"settings-link" + (item.id === section ? " active" : "")}
                aria-current={item.id === section ? "page" : undefined}
                onClick={linkHandler(to)}
              >
                {item.label}
              </a>
            );
          })}
        </nav>
        <div className="settings-body">
          {section === "napcat" ? (
            <NapcatSettings />
          ) : section === "runtime" ? (
            <ConfigSection def={runtimeSection} base={["runtime"]} />
          ) : section === "server" ? (
            <ConfigSection def={serverSection} base={["server"]} />
          ) : section === "config-file" ? (
            <ConfigFile />
          ) : section === "appearance" ? (
            <Appearance />
          ) : (
            <Service />
          )}
        </div>
      </div>
    </div>
  );
}
