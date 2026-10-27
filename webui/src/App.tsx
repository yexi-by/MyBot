import { useEffect } from "react";
import { CircleAlert, Menu, Monitor, Moon, Search, Sun, X } from "lucide-react";
import { CommandPalette } from "./components/CommandPalette";
import { GlobalDialog, Toasts } from "./components/Overlays";
import { ConflictDialog, GlobalBanners, PowerOverlay } from "./components/shell";
import { Button, EmptyState, IconButton, Spinner, StatusDot } from "./components/primitives";
import { getConfig, loadConfig, pollConfig, save, useConfig } from "./lib/config";
import { linkHandler, navigate, useLocation } from "./lib/navigation";
import { runtimeResource, useResource } from "./lib/resource";
import { cycleTheme, themeLabels, useTheme } from "./lib/theme";
import { activeSaver, setConflictDialog, setNav, setPalette, toast, useUi } from "./lib/ui";
import { ConversationsPage } from "./pages/Conversations";
import { OverviewPage } from "./pages/Overview";
import { PromptsPage } from "./pages/Prompts";
import { ProvidersPage } from "./pages/Providers";
import { SandboxPage } from "./pages/Sandbox";
import { SessionsPage } from "./pages/Sessions";
import { SettingsPage } from "./pages/Settings";
import { ToolsPage } from "./pages/Tools";
import { legacyRedirect, navItems, parseRoute } from "./routes";
import type { Route } from "./routes";

function isTyping(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

async function saveConfigNow() {
  const before = getConfig();
  if (!before.loaded) return;
  if (before.conflict) {
    setConflictDialog(true);
    return;
  }
  if (!before.changed.length) {
    toast("没有需要保存的修改");
    return;
  }
  if (await save()) toast("已保存", "success");
  else {
    const after = getConfig();
    if (after.issues.length) toast("有 " + after.issues.length + " 处需要修正后才能保存", "danger");
  }
}

function Logo() {
  return (
    <svg className="logo" viewBox="0 0 24 24" aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="currentColor" />
      <path
        d="M6.5 8a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v4.5a2 2 0 0 1-2 2h-4L8.5 17v-2.5a2 2 0 0 1-2-2Z"
        fill="none"
        stroke="#fff"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const themeIcons = { system: Monitor, light: Sun, dark: Moon };

function NapcatStatus() {
  const runtime = useResource(runtimeResource, 10_000);
  const bots = runtime.data?.napcat.connected ?? [];
  const known = runtime.data !== null;
  const text = !known ? "QQ 状态未知" : bots.length ? "QQ 已连接" : "QQ 未连接";
  return (
    <a
      className="sidebar-status"
      href="/settings/napcat"
      onClick={linkHandler("/settings/napcat", () => setNav(false))}
      title={bots.length ? text + "：" + bots.join("、") : text}
    >
      <StatusDot tone={bots.length ? "success" : known ? "warning" : "neutral"} />
      <span className="sidebar-status-text">
        <span>{text}</span>
        {bots.length > 0 && <span className="muted num">{bots.join("、")}</span>}
      </span>
    </a>
  );
}

function Sidebar({ route }: { route: Route }) {
  const theme = useTheme();
  const ThemeIcon = themeIcons[theme];
  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="sidebar-head">
        <a className="brand" href="/" onClick={linkHandler("/", () => setNav(false))}>
          <Logo />
          <span className="brand-name">QQ Agent Harness</span>
        </a>
        <IconButton className="sidebar-close" icon={X} label="关闭导航" onClick={() => setNav(false)} />
      </div>
      <button
        type="button"
        className="sidebar-search"
        onClick={() => {
          setNav(false);
          setPalette(true);
        }}
        title="搜索或跳转（Ctrl+K）"
      >
        <Search size={15} aria-hidden="true" />
        <span className="sidebar-search-text">搜索或跳转</span>
        <kbd>Ctrl K</kbd>
      </button>
      <nav className="nav">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = route.page === item.page;
          return (
            <a
              key={item.path}
              href={item.path}
              className={"nav-item" + (active ? " active" : "")}
              aria-current={active ? "page" : undefined}
              title={item.label}
              onClick={linkHandler(item.path, () => setNav(false))}
            >
              <Icon size={18} aria-hidden="true" />
              <span className="nav-label">{item.label}</span>
            </a>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <NapcatStatus />
        <IconButton
          icon={ThemeIcon}
          label={"主题：" + themeLabels[theme] + "，点击切换"}
          onClick={cycleTheme}
        />
      </div>
    </aside>
  );
}

function TopBar() {
  return (
    <header className="topbar">
      <IconButton icon={Menu} label="打开导航" onClick={() => setNav(true)} />
      <a className="brand topbar-title" href="/" onClick={linkHandler("/")}>
        <Logo />
        <span className="brand-name">QQ Agent Harness</span>
      </a>
      <IconButton icon={Search} label="搜索或跳转" onClick={() => setPalette(true)} />
    </header>
  );
}

function Unavailable() {
  const state = useConfig();
  if (state.loading || (!state.error && !state.offline))
    return (
      <div className="page-center">
        <Spinner label="正在读取配置…" />
      </div>
    );
  return (
    <div className="page-center">
      <EmptyState
        icon={CircleAlert}
        title={state.offline ? "无法连接控制台服务" : "暂时无法读取配置"}
        action={
          <div className="row">
            <Button onClick={() => void loadConfig()}>重试</Button>
            {state.repairPath && (
              <Button variant="primary" onClick={() => void navigate("/settings/config-file")}>
                修复配置文件
              </Button>
            )}
          </div>
        }
      >
        {state.offline ? "请确认 Harness 正在运行，页面会自动重试。" : state.error}
      </EmptyState>
    </div>
  );
}

function Page({ route }: { route: Route }) {
  const state = useConfig();
  // 配置文件原文和外观设置不依赖解析后的配置，配置损坏时也要能打开。
  if (route.page === "settings" && (route.section === "config-file" || route.section === "appearance"))
    return <SettingsPage section={route.section} />;
  if (!state.loaded) return <Unavailable />;
  switch (route.page) {
    case "overview":
      return <OverviewPage />;
    case "sessions":
      return <SessionsPage route={route} />;
    case "conversations":
      return <ConversationsPage route={route} />;
    case "providers":
      return <ProvidersPage id={route.id} />;
    case "prompts":
      return <PromptsPage path={route.path} />;
    case "tools":
      return <ToolsPage route={route} />;
    case "sandbox":
      return <SandboxPage view={route.view} />;
    case "settings":
      return <SettingsPage section={route.section} />;
    default:
      return (
        <div className="page-center">
          <EmptyState
            icon={CircleAlert}
            title="页面不存在"
            action={<Button onClick={() => void navigate("/")}>返回概览</Button>}
          >
            地址可能已经变更，可以用 Ctrl+K 搜索要找的内容。
          </EmptyState>
        </div>
      );
  }
}

export default function App() {
  const location = useLocation();
  const state = useConfig();
  const ui = useUi();
  const route = parseRoute(location);

  useEffect(() => {
    const target = legacyRedirect(location);
    if (target) void navigate(target, { replace: true });
  }, [location]);

  useEffect(() => {
    void loadConfig();
    const timer = setInterval(() => {
      if (!document.hidden) void pollConfig();
    }, 5000);
    const visible = () => {
      if (!document.hidden) void pollConfig();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, []);

  useEffect(() => {
    if (state.repairPath) void navigate("/settings/config-file", { replace: true });
  }, [state.repairPath]);

  const conflicted = !!state.conflict;
  useEffect(() => {
    if (conflicted) setConflictDialog(true);
  }, [conflicted]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (mod && key === "k") {
        event.preventDefault();
        setPalette(true);
      } else if (mod && key === "s") {
        event.preventDefault();
        const saver = activeSaver();
        if (saver) saver();
        else void saveConfigNow();
      } else if (key === "/" && !mod && !event.altKey && !isTyping(event.target)) {
        const input = document.querySelector<HTMLInputElement>("[data-page-search]");
        if (input) {
          event.preventDefault();
          input.focus();
          input.select();
        }
      } else if (key === "escape") setNav(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={"app" + (ui.nav ? " nav-open" : "")}>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Sidebar route={route} />
      <div className="nav-scrim" aria-hidden="true" onClick={() => setNav(false)} />
      <div className="main">
        <TopBar />
        <GlobalBanners />
        <main id="main" className="content" tabIndex={-1}>
          <Page route={route} />
        </main>
      </div>
      <CommandPalette />
      <ConflictDialog />
      <GlobalDialog />
      <Toasts />
      <PowerOverlay />
    </div>
  );
}
