import { useEffect, useMemo, useState } from "react";
import { errorMessage, post, request } from "./lib/api";
import {
  discardConflict,
  getConfig,
  loadConfig,
  overwriteConflict,
  pollConfig,
  save,
  useConfig,
} from "./lib/config";
import {
  fieldId,
  fieldRoute,
  navigate,
  pageForPath,
  pages,
  useLocation,
} from "./lib/navigation";
import { readTheme, setTheme } from "./lib/theme";
import type { Theme } from "./lib/theme";
import { answer, ask, dismissNotice, notify, useUi } from "./lib/ui";
import type { ConfigEnvelope } from "./lib/types";
import { fieldInstances } from "./schema/fields";
import { Dialog } from "./components/Dialog";
import {
  ConnectionsPage,
  GroupsPage,
  McpPage,
  PersonaPage,
  PrivatePage,
  ToolsPage,
} from "./pages/ConfigPages";
import { FilesPage } from "./pages/FilesPage";
import { SandboxPage } from "./pages/SandboxPage";
import { RuntimePage } from "./pages/RuntimePage";

async function focusField(path: (string | number)[]) {
  await navigate(fieldRoute(path));
  setTimeout(() => {
    const element = document.getElementById(fieldId(path));
    element?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      block: "center",
    });
    element?.focus({ preventScroll: true });
  }, 100);
}
export default function App() {
  const state = useConfig(),
    ui = useUi(),
    location = useLocation(),
    url = new URL(location, window.location.origin),
    pathname = url.pathname,
    page =
      pathname === "/files"
        ? {
            path: "/files",
            name: "文本文件",
            code: "06",
            description: "配置目录中的人格与知识文件",
          }
        : pageForPath(pathname);
  const [filter, setFilter] = useState(""),
    [drawer, setDrawer] = useState(false),
    [statusOpen, setStatusOpen] = useState(false),
    [conflictOpen, setConflictOpen] = useState(false),
    [theme, updateTheme] = useState<Theme>(readTheme),
    [palette, setPalette] = useState(false),
    [query, setQuery] = useState(""),
    [paletteFiles, setPaletteFiles] = useState<string[]>([]),
    [promptInput, setPromptInput] = useState(""),
    [powerBusy, setPowerBusy] = useState(false);
  useEffect(() => {
    void loadConfig();
    const poll = setInterval(() => void pollConfig(), 5000);
    return () => clearInterval(poll);
  }, []);
  useEffect(() => {
    if (pathname === "/") navigate("/connections");
    setFilter("");
    setDrawer(false);
  }, [pathname]);
  useEffect(() => {
    if (state.conflict) setConflictOpen(true);
  }, [state.conflict]);
  useEffect(() => {
    if (
      state.repairPath &&
      (pathname !== "/files" ||
        new URL(location, window.location.origin).searchParams.get("path") !==
          state.repairPath)
    )
      void navigate("/files?path=" + encodeURIComponent(state.repairPath));
  }, [state.repairPath, pathname, location]);
  useEffect(() => {
    setPromptInput(ui.prompt?.input || "");
  }, [ui.prompt]);
  useEffect(() => {
    if (palette) {
      setQuery("");
      request<{ files: string[] }>("/api/files/list")
        .then((data) => setPaletteFiles(data.files))
        .catch(() => {});
    }
  }, [palette]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "s" &&
        pathname !== "/files"
      ) {
        event.preventDefault();
        void save();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((current) => !current);
      }
      if (
        event.key === "/" &&
        !(
          event.target instanceof HTMLElement &&
          (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) ||
            event.target.isContentEditable)
        )
      ) {
        event.preventDefault();
        document.getElementById("field-filter")?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [pathname]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (state.changed.length) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [state.changed.length]);
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    state.changed.forEach((path) => {
      const route = fieldRoute(path).split("?")[0];
      result[route] = (result[route] || 0) + 1;
    });
    return result;
  }, [state.changed]);
  const fileStatus = pathname === "/files" ? ui.fileStatus : null;
  const status = fileStatus
    ? fileStatus.conflict
      ? "外部修改"
      : fileStatus.saving
        ? "写入中"
        : fileStatus.error || state.repairPath
          ? "待修正"
          : fileStatus.dirty
            ? "待写入"
            : "已写入"
    : state.conflict
      ? "外部修改"
      : state.saving
        ? "写入中"
        : state.issues.length
          ? "待修正"
          : state.changed.length
            ? "待写入"
            : state.loaded
              ? "已写入"
              : "未连接";
  const restart = Array.isArray(state.meta.restart_required)
    ? state.meta.restart_required.length > 0
    : state.meta.restart_required;
  const power = async (action: "restart" | "shutdown") => {
    if (
      (await ask(
        action === "restart" ? "重启 Harness" : "关闭 Harness",
        action === "restart"
          ? "当前活动轮次和等待队列会丢失，共享工作区文件保留。进程重新启动取决于部署的守护策略。"
          : "当前活动将停止。若部署配置了自动拉起，进程可能重新启动。",
        { danger: true, confirm: action === "restart" ? "重启" : "关机" },
      )) !== true
    )
      return;
    if (state.changed.length && !(await save())) {
      notify("请先处理尚未写入的配置");
      return;
    }
    setPowerBusy(true);
    try {
      const oldBoot = state.meta.boot_id;
      await post("/api/system/" + action);
      if (action === "shutdown") {
        notify("已请求关机，正在等待服务停止");
        return;
      }
      notify("已请求重启，等待新进程接管…");
      const deadline = Date.now() + 90_000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        try {
          const result = await request<ConfigEnvelope>("/api/config");
          if (result.meta.boot_id !== oldBoot) {
            await loadConfig();
            notify("新进程已接管");
            return;
          }
        } catch {
          /* 停机期间连接暂不可用。 */
        }
      }
      notify("尚未检测到新进程，请检查外部守护策略");
    } catch (error) {
      notify(errorMessage(error));
    } finally {
      setPowerBusy(false);
    }
  };
  const commands = [
    ...pages.map((item) => ({
      title: item.name,
      detail: item.description,
      run: () => navigate(item.path),
    })),
    ...fieldInstances(state.draft).map((item) => ({
      title: item.field.label,
      detail: item.path.join("."),
      run: () => focusField(item.path),
    })),
    ...paletteFiles.map((path) => ({
      title: path,
      detail: "配置文件",
      run: () => navigate("/files?path=" + encodeURIComponent(path)),
    })),
    { title: "立即写入配置", detail: "Ctrl + S", run: () => void save() },
    {
      title: "切换亮 / 暗主题",
      detail: "外观",
      run: () => {
        const next = theme === "dark" ? "light" : "dark";
        setTheme(next);
        updateTheme(next);
      },
    },
    { title: "重启 Harness", detail: "电源", run: () => void power("restart") },
  ]
    .filter(
      (command) =>
        !query ||
        (command.title + " " + command.detail)
          .toLowerCase()
          .includes(query.toLowerCase()),
    )
    .slice(0, 40);
  return (
    <div className="app-shell">
      <aside className={"sidebar " + (drawer ? "open" : "")}>
        <button className="brand" onClick={() => navigate("/runtime")}>
          <span className="brand-symbol">Q</span>
          <span>
            <strong>QQ Agent</strong>
            <small>HARNESS / CONTROL</small>
          </span>
        </button>
        <div className="sidebar-caption">
          CONTROL PANEL <span>v1.0</span>
        </div>
        <nav>
          {pages.map((item) => (
            <button
              key={item.path}
              className={
                pathname.startsWith(item.path) ||
                (pathname === "/files" && item.path === "/persona")
                  ? "active"
                  : ""
              }
              onClick={() => navigate(item.path)}
            >
              <span className="nav-code">{item.code}</span>
              <span>{item.name}</span>
              {counts[item.path] ? (
                <span className="count">{counts[item.path]}</span>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="dot ok" /> 配置文件是唯一事实来源
          <small>
            运行状态留在内存
            <br />
            共享文件独立持久化
          </small>
        </div>
      </aside>
      {drawer && (
        <button
          className="drawer-scrim"
          aria-label="关闭导航"
          onClick={() => setDrawer(false)}
        />
      )}
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button menu-button"
            aria-label="打开导航"
            onClick={() => setDrawer(!drawer)}
          >
            ☰
          </button>
          <div className="breadcrumb">
            控制台 <span>/</span> <strong>{page.name}</strong>
          </div>
          <div className="top-actions">
            <button
              className={
                "status-pill " +
                ((
                  fileStatus
                    ? fileStatus.conflict ||
                      fileStatus.error ||
                      state.repairPath
                    : state.conflict || state.issues.length
                )
                  ? "bad"
                  : (fileStatus ? fileStatus.dirty : state.changed.length)
                    ? "pending"
                    : "ok")
              }
              onClick={() => {
                if (fileStatus?.conflict) fileStatus.resolve();
                else if (fileStatus?.dirty) fileStatus.save();
                else setStatusOpen(true);
              }}
            >
              <span className="dot" />
              {status}
              {!fileStatus && state.changed.length
                ? " · " + state.changed.length
                : ""}
            </button>
            {state.meta.sandbox_restart_required ? (
              <span
                className="restart-badge"
                title="需重建 Runner，再重启 Harness；单独重启 Harness 不会应用沙箱资源变更"
              >
                沙箱待重建
              </span>
            ) : (
              restart && <span className="restart-badge">待重启</span>
            )}
            <button
              className="shortcut-button"
              onClick={() => setPalette(true)}
              aria-label="打开命令面板"
            >
              ⌕ <kbd>Ctrl K</kbd>
            </button>
            <select
              className="theme-select"
              aria-label="主题"
              value={theme}
              onChange={(event) => {
                const value = event.target.value as Theme;
                setTheme(value);
                updateTheme(value);
              }}
            >
              <option value="system">跟随系统</option>
              <option value="light">亮色</option>
              <option value="dark">暗色</option>
            </select>
            <button
              className="icon-button"
              title="重启"
              aria-label="重启 Harness"
              onClick={() => void power("restart")}
              disabled={powerBusy}
            >
              ↻
            </button>
            <button
              className="icon-button"
              title="关机"
              aria-label="关闭 Harness"
              onClick={() => void power("shutdown")}
              disabled={powerBusy}
            >
              ⏻
            </button>
          </div>
        </header>
        <div className="content-layout">
          <main>
            <div className="page-header">
              <div className="eyebrow">
                [{page.path.slice(1)}] <span>QQ AGENT HARNESS</span>
              </div>
              <div className="page-title-row">
                <div>
                  <h1>{page.name}</h1>
                  <p>{page.description}</p>
                </div>
                <span className="page-number">{page.code}</span>
              </div>
              <div className="page-bottom">
                <span className="page-change">
                  {counts[page.path]
                    ? counts[page.path] + " 项待写入"
                    : "配置与运行分离"}
                </span>
                {pathname !== "/files" && (
                  <div className="filter-input">
                    <span>⌕</span>
                    <input
                      id="field-filter"
                      value={filter}
                      onChange={(event) => setFilter(event.target.value)}
                      placeholder="筛选本页字段或工具…"
                      aria-label="筛选本页字段"
                    />
                    <kbd>/</kbd>
                    {filter && (
                      <button
                        aria-label="清除筛选"
                        onClick={() => setFilter("")}
                      >
                        ×
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
            {state.error && (
              <p className="notice bad">
                {state.error}
                {state.conflict && (
                  <button
                    className="small"
                    onClick={() => setConflictOpen(true)}
                  >
                    处理冲突
                  </button>
                )}
              </p>
            )}
            {state.issues.length > 0 && (
              <div className="issue-summary">
                {state.issues.map((issue, index) => (
                  <button key={index} onClick={() => focusField(issue.path)}>
                    {issue.path.join(".")} · {issue.message}
                  </button>
                ))}
              </div>
            )}
            {pathname === "/files" ? (
              <FilesPage
                key={url.searchParams.get("path") || ""}
                path={url.searchParams.get("path") || ""}
              />
            ) : !state.loaded ? (
              <div className="empty large">
                <h2>
                  {state.loading ? "正在连接配置服务…" : "暂时无法读取配置"}
                </h2>
                <p>配置文件损坏时，可直接进入文本编辑器修复。</p>
                <div className="button-row">
                  <button onClick={() => void loadConfig()}>重新连接</button>
                  <button onClick={() => navigate("/files?path=harness.toml")}>
                    修复配置原文
                  </button>
                </div>
              </div>
            ) : pathname.startsWith("/groups") ? (
              <GroupsPage filter={filter} query={url.searchParams} />
            ) : pathname.startsWith("/private") ? (
              <PrivatePage filter={filter} />
            ) : pathname.startsWith("/tools") ? (
              <ToolsPage filter={filter} />
            ) : pathname.startsWith("/mcp") ? (
              <McpPage filter={filter} query={url.searchParams} />
            ) : pathname.startsWith("/persona") ? (
              <PersonaPage filter={filter} />
            ) : pathname.startsWith("/sandbox") ? (
              <SandboxPage filter={filter} />
            ) : pathname.startsWith("/runtime") ? (
              <RuntimePage filter={filter} />
            ) : (
              <ConnectionsPage filter={filter} query={url.searchParams} />
            )}
            <footer className="page-footer">
              <span>QQ Agent Harness</span>
              <span>无聊天数据库 · 显式动作 · 按群授权</span>
            </footer>
          </main>
          <aside className="right-rail">
            <section>
              <h3>本页目录</h3>
              <PageToc
                location={location}
                loaded={state.loaded}
                filter={filter}
              />
            </section>
            <section>
              <h3>
                写回记录 <span>{state.receipts.length}</span>
              </h3>
              {state.receipts.length ? (
                state.receipts.slice(0, 8).map((receipt, i) => (
                  <div className="receipt" key={receipt.at + i}>
                    <time>{receipt.at}</time>
                    <strong>{receipt.message}</strong>
                    <p>
                      {receipt.fields.slice(0, 5).join("、")}
                      {receipt.fields.length > 5 ? "等" : ""}
                    </p>
                  </div>
                ))
              ) : (
                <p className="muted">本次打开后的配置写回会显示在这里。</p>
              )}
            </section>
            <section className="rail-note">
              <span>设计边界</span>
              <p>
                群配置授予 Agent 能力。是否行动，由 Agent 依据对话和规则判断。
              </p>
            </section>
          </aside>
        </div>
      </div>
      <Dialog
        open={statusOpen}
        title={fileStatus ? "文本写回状态" : "配置写回状态"}
        onClose={() => setStatusOpen(false)}
      >
        <div className="dialog-body">
          <p>
            <strong>{status}</strong> · 配置修订 {state.meta.revision}
          </p>
          {restart && (
            <p className="notice">连接或启动设置已修改，需要重启生效。</p>
          )}
          {state.meta.sandbox_restart_required && (
            <p className="notice">
              沙箱资源设置尚未在 Runner 应用。请先重建 Runner，再重启
              Harness；本页的重启按钮不会单独重建 Runner。
            </p>
          )}
          {(fileStatus?.error || state.error) && (
            <p className="notice bad">{fileStatus?.error || state.error}</p>
          )}
          <p className="muted">输入停止约一秒后自动写入；Ctrl + S 立即保存。</p>
          <div className="receipt-list">
            {state.receipts.slice(0, 10).map((receipt, index) => (
              <div className="receipt" key={index}>
                <time>{receipt.at}</time>
                <strong>{receipt.message}</strong>
                <p>{receipt.fields.join("、")}</p>
              </div>
            ))}
          </div>
        </div>
        <div className="dialog-actions">
          <button onClick={() => setStatusOpen(false)}>关闭</button>
          {fileStatus ? (
            <button
              className="primary"
              disabled={fileStatus.saving || !fileStatus.dirty}
              onClick={fileStatus.save}
            >
              立即写入
            </button>
          ) : state.conflict ? (
            <button
              className="primary"
              onClick={() => {
                setStatusOpen(false);
                setConflictOpen(true);
              }}
            >
              处理外部修改
            </button>
          ) : (
            <button
              className="primary"
              disabled={state.saving || !state.changed.length}
              onClick={() => void save()}
            >
              立即写入
            </button>
          )}
        </div>
      </Dialog>
      <Dialog
        open={conflictOpen && !!state.conflict}
        title="配置发生外部修改"
        onClose={() => setConflictOpen(false)}
      >
        <div className="dialog-body">
          <p>已读取最新版本标识，你的表单内容保持不变。</p>
          <p className="muted">
            继续编辑会暂停自动写回，直到选择覆盖或丢弃。覆盖时还会再次检查最新版本。
          </p>
        </div>
        <div className="dialog-actions">
          <button onClick={() => setConflictOpen(false)}>继续编辑</button>
          <button
            onClick={() => {
              discardConflict();
              setConflictOpen(false);
            }}
          >
            丢弃并刷新
          </button>
          <button
            className="primary"
            onClick={async () => {
              try {
                await overwriteConflict();
                if (!getConfig().conflict) setConflictOpen(false);
              } catch (error) {
                notify(errorMessage(error));
              }
            }}
          >
            覆盖外部修改
          </button>
        </div>
      </Dialog>
      <Dialog
        open={palette}
        title="跳转、搜索与操作"
        onClose={() => setPalette(false)}
        className="command-dialog"
      >
        <div className="command-search">
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索页面、字段、文件或操作…"
            aria-label="命令搜索"
            onKeyDown={(event) => {
              if (event.key === "Enter" && commands[0]) {
                event.preventDefault();
                event.stopPropagation();
                setPalette(false);
                commands[0].run();
              }
            }}
          />
        </div>
        <div className="commands">
          {commands.map((command, index) => (
            <button
              key={command.title + index}
              onClick={() => {
                setPalette(false);
                command.run();
              }}
            >
              <span>{command.title}</span>
              <small>{command.detail}</small>
              <span>↗</span>
            </button>
          ))}
          {!commands.length && <p className="empty">没有匹配结果。</p>}
        </div>
      </Dialog>
      <Dialog
        open={!!ui.prompt}
        title={ui.prompt?.title || ""}
        onClose={() => answer(false)}
      >
        <div className="dialog-body">
          <p>{ui.prompt?.message}</p>
          {ui.prompt?.input !== undefined && (
            <input
              autoFocus
              value={promptInput}
              onChange={(event) => setPromptInput(event.target.value)}
              placeholder={ui.prompt.placeholder}
              aria-label={ui.prompt.title}
              onKeyDown={(event) => {
                if (event.key === "Enter" && promptInput.trim()) {
                  event.preventDefault();
                  event.stopPropagation();
                  answer(promptInput.trim());
                }
              }}
            />
          )}
        </div>
        <div className="dialog-actions">
          <button onClick={() => answer(false)}>取消</button>
          <button
            className={ui.prompt?.danger ? "danger" : "primary"}
            disabled={ui.prompt?.input !== undefined && !promptInput.trim()}
            onClick={() =>
              answer(ui.prompt?.input !== undefined ? promptInput.trim() : true)
            }
          >
            {ui.prompt?.confirm}
          </button>
        </div>
      </Dialog>
      {ui.notice && (
        <div className="toast" role="status">
          <span>{ui.notice}</span>
          <button aria-label="关闭通知" onClick={dismissNotice}>
            ×
          </button>
        </div>
      )}
    </div>
  );
}
function PageToc({
  location,
  loaded,
  filter,
}: {
  location: string;
  loaded: boolean;
  filter: string;
}) {
  const [items, setItems] = useState<{ id: string; title: string }[]>([]);
  useEffect(() => {
    const collect = () =>
      setItems(
        [...document.querySelectorAll<HTMLElement>("main .sec[id]")].map(
          (element) => ({
            id: element.id,
            title: element.querySelector("h2")?.textContent || element.id,
          }),
        ),
      );
    const timer = setTimeout(collect, 100);
    const observer = new MutationObserver(collect);
    const main = document.querySelector("main");
    if (main) observer.observe(main, { childList: true, subtree: true });
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [location, loaded, filter]);
  return (
    <div className="toc">
      {items.map((item) => (
        <a
          key={item.id}
          href={"#" + item.id}
          onClick={(event) => {
            event.preventDefault();
            document.getElementById(item.id)?.scrollIntoView({
              behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
                .matches
                ? "instant"
                : "smooth",
              block: "start",
            });
          }}
        >
          {item.title}
        </a>
      ))}
    </div>
  );
}
