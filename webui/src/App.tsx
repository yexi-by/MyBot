import { Cat, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { CommandPalette } from "@/components/CommandPalette";
import { ConfirmHost } from "@/components/Dialog";
import { ConfigConflictDialog, PowerLayer } from "@/components/Overlays";
import { Rail, Sidebar, TopBar } from "@/components/Shell";
import { Toaster } from "@/components/Toaster";
import { config, useConfig } from "@/lib/config";
import { CONFIG_FILE, files, useFiles } from "@/lib/files";
import { cls } from "@/lib/pages";
import { navigate, routerStore, useRoute } from "@/lib/router";
import { setMenu, togglePalette, useUi } from "@/lib/ui";
import FilesPage from "@/pages/FilesPage";
import McpPage from "@/pages/McpPage";
import PluginPage from "@/pages/PluginPage";
import ProvidersPage from "@/pages/ProvidersPage";
import SystemPage from "@/pages/SystemPage";
import type { PageKey } from "@/schema";

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function Boot({ error }: { error?: string }) {
  return (
    <div className="boot">
      <div className="boot-card" role={error ? "alert" : "status"}>
        <span className="brand-mark" aria-hidden>
          <Cat className="ic" />
        </span>
        <p className="boot-title">MyBot</p>
        {error ? (
          <>
            <p className="boot-sub">读不到配置：{error}</p>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => {
                config.store.set({ phase: "loading" });
                void config.load();
              }}
            >
              <RefreshCw className="ic" aria-hidden />
              重试
            </button>
          </>
        ) : (
          <>
            <p className="boot-sub">正在读取 mybot.toml…</p>
            <span className="boot-bar" aria-hidden />
          </>
        )}
      </div>
    </div>
  );
}

function BrokenNotice() {
  const message = useConfig((state) => state.loadError);
  return (
    <div className="page-inner">
      <div className="empty" role="alert">
        <p className="empty-title">mybot.toml 语法有误，配置页暂时不可用</p>
        <p className="empty-desc">{message}</p>
        <p className="empty-desc">
          <button type="button" className="btn btn--primary" onClick={() => navigate({ page: "files", file: CONFIG_FILE })}>
            打开原文修复
          </button>
        </p>
      </div>
    </div>
  );
}

function ConfigPage({ page }: { page: PageKey }) {
  if (page === "system") return <SystemPage />;
  if (page === "providers") return <ProvidersPage />;
  if (page === "mcp") return <McpPage />;
  if (page.startsWith("plugin:")) {
    const id = page.slice(7) as Parameters<typeof PluginPage>[0]["id"];
    return <PluginPage key={id} id={id} />;
  }
  return null;
}

function FileOptions() {
  const list = useFiles((state) => state.list);
  return (
    <datalist id="config-files">
      {(list ?? []).map((path) => (
        <option key={path} value={path} />
      ))}
    </datalist>
  );
}

export default function App() {
  const phase = useConfig((state) => state.phase);
  const loadError = useConfig((state) => state.loadError);
  const route = useRoute();
  const menu = useUi((state) => state.menu);
  const [filesVisited, setFilesVisited] = useState(route.page === "files");
  const onFiles = route.page === "files";

  useEffect(() => {
    void config.load();
  }, []);

  if (onFiles && !filesVisited) setFilesVisited(true);

  // mybot.toml 语法错误时直接带到原文修复。
  useEffect(() => {
    if (phase === "broken" && routerStore.get().route.page !== "files") {
      navigate({ page: "files", file: CONFIG_FILE }, { replace: true });
    }
  }, [phase]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (mod && key === "s") {
        event.preventDefault();
        const page = routerStore.get().route.page;
        if (page === "files" || config.state.phase === "broken") void files.saveNow();
        else void config.saveNow();
      } else if (mod && key === "k") {
        event.preventDefault();
        togglePalette();
      } else if (event.key === "/" && !mod && !event.altKey && !isTyping(event.target)) {
        const filter = document.getElementById("page-filter");
        if (filter && filter.offsetParent !== null) {
          event.preventDefault();
          filter.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (config.isDirty() || config.isBusy() || files.isDirty() || files.isBusy()) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  if (phase === "loading") return <Boot />;
  if (phase === "failed") return <Boot error={loadError} />;

  return (
    <>
      <a className="skip" href="#page">
        跳到主要内容
      </a>
      <div className={cls("app", menu && "is-menu")}>
        <Sidebar />
        <div className="scrim" aria-hidden onClick={() => setMenu(false)} />
        <div className="main">
          <TopBar />
          <div className={cls("body", onFiles && "body--wide")}>
            <main className="page" id="page" tabIndex={-1} data-page={route.page}>
              {onFiles ? null : phase === "broken" ? <BrokenNotice /> : <ConfigPage page={route.page} />}
              {filesVisited ? <FilesPage active={onFiles} /> : null}
            </main>
            <Rail />
          </div>
        </div>
      </div>
      <FileOptions />
      <CommandPalette />
      <ConfirmHost />
      <ConfigConflictDialog />
      <PowerLayer />
      <Toaster />
    </>
  );
}
