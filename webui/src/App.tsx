/** MyBot 配置控制台主框架：导航、自动保存、热生效反馈与冲突处理。 */

import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { Loader2, Power, RotateCw } from "lucide-react";
import { useTheme } from "next-themes";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Toaster } from "@/components/ui/sonner";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  ApiError,
  getConfig,
  restartSystem,
  saveConfig,
  shutdownSystem,
  validateConfig,
} from "@/lib/api";
import { PLUGIN_METAS, SECTION_LABELS } from "@/lib/configMeta";
import type {
  ConfigGetResponse,
  ConfigIssuePayload,
  MyBotConfigData,
  PluginId,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const FilesPage = lazy(() => import("@/pages/FilesPage"));
const McpPage = lazy(() => import("@/pages/McpPage"));
const ProvidersPage = lazy(() => import("@/pages/ProvidersPage"));
const SystemPage = lazy(() => import("@/pages/SystemPage"));
const PluginPage = lazy(() => import("@/pages/plugins/PluginPage"));

type PageKey = "system" | "providers" | "mcp" | "files" | `plugin:${PluginId}`;

interface NavItem {
  key: PageKey;
  label: string;
}

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "系统",
    items: [
      { key: "system", label: "系统设置" },
      { key: "providers", label: "模型 Providers" },
      { key: "mcp", label: "MCP 服务" },
    ],
  },
  {
    title: "插件",
    items: PLUGIN_METAS.map((meta) => ({
      key: `plugin:${meta.id}` as PageKey,
      label: meta.name,
    })),
  },
  {
    title: "配置文本",
    items: [{ key: "files", label: "Prompt 与知识库" }],
  },
];

const PAGE_KEYS = new Set<string>(
  NAV_GROUPS.flatMap((group) => group.items.map((item) => item.key)),
);

function pageFromLocation(): PageKey {
  const requested = new URLSearchParams(window.location.search).get("page") ?? "";
  return PAGE_KEYS.has(requested) ? (requested as PageKey) : "system";
}

type ApplyState =
  | { kind: "idle" }
  | { kind: "editing" }
  | { kind: "saving" }
  | { kind: "watching" }
  | { kind: "applied" }
  | { kind: "saved" }
  | { kind: "invalid" }
  | { kind: "error" };

type PowerState =
  | { kind: "idle" }
  | { kind: "restarting" }
  | { kind: "restart_timeout" }
  | { kind: "shutdown" };

const CONFIG_AUTOSAVE_DELAY_MS = 800;
const RESTART_POLL_LIMIT_MS = 60_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function App() {
  const [snapshot, setSnapshot] = useState<ConfigGetResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [page, setPage] = useState<PageKey>(pageFromLocation);
  const [saving, setSaving] = useState(false);
  const [serverIssues, setServerIssues] = useState<ConfigIssuePayload[]>([]);
  const [restartSections, setRestartSections] = useState<string[]>([]);
  const [applyState, setApplyState] = useState<ApplyState>({ kind: "idle" });
  const [conflictOpen, setConflictOpen] = useState(false);
  const [powerState, setPowerState] = useState<PowerState>({ kind: "idle" });
  const [powerConfirm, setPowerConfirm] = useState<"restart" | "shutdown" | null>(
    null,
  );
  const pollGeneration = useRef(0);
  const powerPollGeneration = useRef(0);
  const mainContent = useRef<HTMLElement>(null);
  const editVersion = useRef(0);
  const blockedVersion = useRef<number | null>(null);
  const { resolvedTheme } = useTheme();

  const methods = useForm<MyBotConfigData>({
    values: snapshot?.config ?? ({} as MyBotConfigData),
  });
  const isDirty = methods.formState.isDirty;
  const watchedConfig = useWatch({ control: methods.control });

  const reload = useCallback(async () => {
    const fresh = await getConfig();
    blockedVersion.current = null;
    methods.reset(fresh.config);
    setSnapshot(fresh);
    setRestartSections(fresh.meta.restart_required_sections);
    setServerIssues(fresh.issues);
    setApplyState({ kind: "idle" });
  }, [methods]);

  useEffect(() => {
    getConfig()
      .then((fresh) => {
        setSnapshot(fresh);
        setRestartSections(fresh.meta.restart_required_sections);
        setServerIssues(fresh.issues);
      })
      .catch((error: unknown) => {
        setLoadError(error instanceof Error ? error.message : "加载配置失败");
      });
  }, []);

  useEffect(() => {
    const handlePopState = () => setPage(pageFromLocation());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigate = useCallback((nextPage: PageKey) => {
    if (nextPage === page) return;
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("page", nextPage);
    window.history.pushState({}, "", nextUrl);
    setPage(nextPage);
  }, [page]);

  useEffect(() => {
    mainContent.current?.focus();
  }, [page]);

  useEffect(() => {
    if (isDirty) {
      editVersion.current += 1;
    }
  }, [watchedConfig, isDirty]);

  /** 把后端校验错误映射到表单字段，并在顶栏展示完整列表。 */
  const applyIssues = useCallback(
    (issues: ConfigIssuePayload[]) => {
      methods.clearErrors();
      for (const issue of issues) {
        methods.setError(issue.location as never, {
          type: issue.error_type,
          message: issue.message,
        });
      }
      setServerIssues(issues);
    },
    [methods],
  );

  /** 保存后轮询插件配置版本，反馈热生效状态。 */
  const pollApply = useCallback(
    async (previousRevision: number) => {
      const generation = ++pollGeneration.current;
      setApplyState({ kind: "watching" });
      for (let attempt = 0; attempt < 10; attempt += 1) {
        await sleep(1000);
        if (pollGeneration.current !== generation) return;
        try {
          const fresh = await getConfig();
          setSnapshot((previous) =>
            previous ? { ...previous, meta: fresh.meta } : previous,
          );
          setRestartSections(fresh.meta.restart_required_sections);
          if (fresh.meta.plugin_revision > previousRevision) {
            setApplyState({ kind: "applied" });
            return;
          }
        } catch {
          // 单次轮询失败不影响后续
        }
      }
      setApplyState({ kind: "saved" });
    },
    [],
  );

  const onSave = useCallback(async () => {
    if (!snapshot || !isDirty || saving) return;
    const payload = methods.getValues();
    const previousRevision = snapshot.meta.plugin_revision;
    const saveVersion = editVersion.current;
    setSaving(true);
    setApplyState({ kind: "saving" });
    try {
      const result = await saveConfig(payload, snapshot.sha256);
      blockedVersion.current = null;
      const pluginConfigChanged =
        JSON.stringify(result.config.plugins ?? {}) !==
        JSON.stringify(snapshot.config.plugins ?? {});
      const unchangedSinceRequest = editVersion.current === saveVersion;
      if (unchangedSinceRequest) {
        methods.reset(result.config);
      }
      setServerIssues([]);
      setSnapshot((previous) =>
        previous
          ? {
              ...previous,
              config: unchangedSinceRequest ? result.config : previous.config,
              sha256: result.sha256,
            }
          : previous,
      );
      setRestartSections(result.restart_required_sections);
      if (!unchangedSinceRequest) {
        setApplyState({ kind: "editing" });
      } else if (snapshot.meta.watcher_active && pluginConfigChanged) {
        void pollApply(previousRevision);
      } else {
        setApplyState({ kind: "saved" });
      }
    } catch (error) {
      blockedVersion.current = saveVersion;
      if (error instanceof ApiError && error.status === 409) {
        setApplyState({ kind: "error" });
        setConflictOpen(true);
      } else if (error instanceof ApiError && error.issues.length > 0) {
        setApplyState({ kind: "invalid" });
        applyIssues(error.issues);
        toast.error(`配置校验失败：${error.issues.length} 个问题`);
      } else {
        setApplyState({ kind: "error" });
        toast.error(error instanceof Error ? error.message : "保存失败");
      }
    } finally {
      setSaving(false);
    }
  }, [snapshot, isDirty, saving, methods, applyIssues, pollApply]);

  useEffect(() => {
    if (
      !snapshot ||
      !isDirty ||
      saving ||
      conflictOpen ||
      powerState.kind !== "idle" ||
      blockedVersion.current === editVersion.current
    ) {
      return;
    }
    pollGeneration.current += 1;
    setApplyState({ kind: "editing" });
    const timer = window.setTimeout(() => {
      void onSave();
    }, CONFIG_AUTOSAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [watchedConfig, snapshot, isDirty, saving, conflictOpen, powerState.kind, onSave]);

  const onValidate = useCallback(async () => {
    try {
      const result = await validateConfig(methods.getValues());
      if (result.valid) {
        methods.clearErrors();
        setServerIssues([]);
        toast.success("校验通过");
      } else {
        applyIssues(result.issues);
        toast.error(`发现 ${result.issues.length} 个配置问题`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "校验请求失败");
    }
  }, [methods, applyIssues]);

  /** 重启后轮询服务恢复：boot_id 变化才说明新进程已接管，避免命中停机中的旧进程。 */
  const waitForRestart = useCallback(
    async (previousBootId: string) => {
      const generation = ++powerPollGeneration.current;
      const deadline = Date.now() + RESTART_POLL_LIMIT_MS;
      await sleep(1500);
      while (Date.now() < deadline) {
        if (powerPollGeneration.current !== generation) return;
        try {
          const fresh = await getConfig();
          if (fresh.meta.boot_id !== previousBootId) {
            setPowerState({ kind: "idle" });
            await reload();
            toast.success("MyBot 已重启完成");
            return;
          }
        } catch {
          // 进程尚未恢复，继续轮询
        }
        await sleep(1500);
      }
      setPowerState({ kind: "restart_timeout" });
    },
    [reload],
  );

  /** 执行确认过的重启/关机操作，并切换到对应的等待界面。 */
  const onPowerAction = useCallback(async () => {
    const action = powerConfirm;
    if (action === null || !snapshot) return;
    setPowerConfirm(null);
    try {
      if (action === "restart") {
        await restartSystem();
      } else {
        await shutdownSystem();
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "电源操作失败");
      return;
    }
    if (action === "shutdown") {
      setPowerState({ kind: "shutdown" });
      return;
    }
    setPowerState({ kind: "restarting" });
    void waitForRestart(snapshot.meta.boot_id);
  }, [powerConfirm, snapshot, waitForRestart]);

  /** 关机/超时后手动尝试重连；连通则恢复正常界面。 */
  const onReconnect = useCallback(async () => {
    try {
      await getConfig();
    } catch {
      toast.error("仍无法连接，MyBot 尚未恢复");
      return;
    }
    powerPollGeneration.current += 1;
    setPowerState({ kind: "idle" });
    await reload();
    toast.success("已重新连接");
  }, [reload]);

  if (loadError) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Alert variant="destructive" className="max-w-md">
          <AlertTitle>配置加载失败</AlertTitle>
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      </div>
    );
  }
  if (!snapshot) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        正在加载配置…
      </div>
    );
  }

  const restartLabels = restartSections.map(
    (section) => SECTION_LABELS[section] ?? section,
  );

  return (
    <FormProvider {...methods}>
      <a
        href="#main-content"
        className="fixed left-3 top-3 z-50 -translate-y-20 rounded-md bg-background px-3 py-2 text-sm shadow focus:translate-y-0"
      >
        跳至主要内容
      </a>
      <div className="flex h-dvh bg-background">
        <aside className="hidden w-52 shrink-0 flex-col border-r md:flex">
          <div className="px-4 py-4">
            <h1 className="text-base font-semibold">MyBot 配置控制台</h1>
            <p className="text-xs text-muted-foreground">
              插件配置版本 v{snapshot.meta.plugin_revision}
            </p>
          </div>
          <Separator />
          <nav className="flex-1 space-y-4 overflow-y-auto p-3">
            {NAV_GROUPS.map((group) => (
              <div key={group.title}>
                <p className="px-2 pb-1 text-xs font-medium text-muted-foreground">
                  {group.title}
                </p>
                <div className="space-y-0.5">
                  {group.items.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      aria-current={page === item.key ? "page" : undefined}
                      onClick={() => navigate(item.key)}
                      className={cn(
                        "w-full cursor-pointer rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent",
                        page === item.key && "bg-accent font-medium",
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </nav>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3 md:px-6">
            <div className="w-full md:hidden">
              <label htmlFor="mobile-page-navigation" className="sr-only">
                当前配置页面
              </label>
              <select
                id="mobile-page-navigation"
                value={page}
                onChange={(event) => navigate(event.target.value as PageKey)}
                className="h-11 w-full cursor-pointer rounded-lg border border-input bg-background px-3 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {NAV_GROUPS.map((group) => (
                  <optgroup key={group.title} label={group.title}>
                    {group.items.map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {snapshot.meta.watcher_active ? null : (
                <Tooltip>
                  <TooltipTrigger>
                    <Badge variant="secondary">开发模式</Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    配置热载未运行，保存只写文件
                  </TooltipContent>
                </Tooltip>
              )}
              {applyState.kind === "idle" ? (
                <Badge variant="outline">自动保存已开启</Badge>
              ) : null}
              {applyState.kind === "editing" ? (
                <Badge variant="secondary">等待自动保存…</Badge>
              ) : null}
              {applyState.kind === "saving" ? (
                <Badge variant="secondary">自动保存中…</Badge>
              ) : null}
              {applyState.kind === "watching" ? (
                <Badge variant="secondary">等待热生效…</Badge>
              ) : null}
              {applyState.kind === "applied" ? (
                <Badge className="bg-green-600 text-white">已热生效</Badge>
              ) : null}
              {applyState.kind === "saved" ? (
                <Badge variant="outline">已自动保存</Badge>
              ) : null}
              {applyState.kind === "invalid" ? (
                <Badge variant="destructive">配置有误，未保存</Badge>
              ) : null}
              {applyState.kind === "error" ? (
                <Badge variant="destructive">自动保存失败</Badge>
              ) : null}
              {restartLabels.length > 0 ? (
                <Tooltip>
                  <TooltipTrigger>
                    <Badge variant="destructive">
                      {restartLabels.length} 项改动需重启
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    需重启进程生效：{restartLabels.join("、")}
                  </TooltipContent>
                </Tooltip>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              {restartLabels.length > 0 ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPowerConfirm("restart")}
                >
                  立即重启
                </Button>
              ) : null}
              <Button variant="outline" size="sm" onClick={onValidate}>
                校验
              </Button>
              <ThemeToggle />
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="重启 MyBot 进程"
                      onClick={() => setPowerConfirm("restart")}
                    />
                  }
                >
                  <RotateCw className="h-4 w-4" />
                </TooltipTrigger>
                <TooltipContent>重启 MyBot 进程</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="关闭 MyBot 进程"
                      onClick={() => setPowerConfirm("shutdown")}
                    />
                  }
                >
                  <Power className="h-4 w-4" />
                </TooltipTrigger>
                <TooltipContent>关闭 MyBot 进程</TooltipContent>
              </Tooltip>
            </div>
          </header>

          {serverIssues.length > 0 ? (
            <Alert variant="destructive" className="m-4 mb-0 w-auto">
              <AlertTitle>配置存在问题</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {serverIssues.map((issue) => (
                    <li key={`${issue.location}:${issue.message}`}>
                      <code className="text-xs">{issue.location}</code>：
                      {issue.message}
                    </li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          ) : null}

          <main
            ref={mainContent}
            id="main-content"
            tabIndex={-1}
            className="flex-1 overflow-y-auto p-4 outline-none md:p-6"
          >
            <div className="mx-auto max-w-7xl space-y-6">
              <Suspense
                fallback={
                  <p className="text-sm text-muted-foreground">正在加载页面…</p>
                }
              >
                {page === "system" ? <SystemPage /> : null}
                {page === "providers" ? <ProvidersPage /> : null}
                {page === "mcp" ? <McpPage /> : null}
                {page === "files" ? <FilesPage /> : null}
                {page.startsWith("plugin:") ? (
                  <PluginPage pluginId={page.slice(7) as PluginId} />
                ) : null}
              </Suspense>
            </div>
          </main>
        </div>
      </div>

      <Dialog open={conflictOpen} onOpenChange={setConflictOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>配置已被外部修改</DialogTitle>
            <DialogDescription>
              自动保存发现配置文件已被其他方式修改。刷新后将丢失当前未保存的修改。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConflictOpen(false)}>
              保留我的修改
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConflictOpen(false);
                void reload();
              }}
            >
              丢弃并刷新
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={powerConfirm !== null}
        onOpenChange={(open) => {
          if (!open) setPowerConfirm(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {powerConfirm === "restart" ? "重启 MyBot？" : "关闭 MyBot？"}
            </DialogTitle>
            <DialogDescription>
              {powerConfirm === "restart"
                ? "进程将优雅退出，由 Docker 等守护策略自动拉起；期间面板会短暂断开，恢复后自动重连。"
                : "进程将优雅退出并保持停止；Docker 部署下会被守护策略重新拉起，效果等同重启。"}
            </DialogDescription>
          </DialogHeader>
          {isDirty || saving ? (
            <p className="text-sm text-muted-foreground">
              存在待自动保存的修改，保存完成后才能执行电源操作…
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPowerConfirm(null)}>
              取消
            </Button>
            <Button
              variant={powerConfirm === "shutdown" ? "destructive" : "default"}
              disabled={isDirty || saving}
              onClick={() => void onPowerAction()}
            >
              {powerConfirm === "restart" ? "确认重启" : "确认关机"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {powerState.kind !== "idle" ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-lg border bg-card p-6 text-card-foreground shadow-lg">
            {powerState.kind === "restarting" ? (
              <div className="flex flex-col items-center gap-3 text-center">
                <Loader2
                  className="h-8 w-8 animate-spin text-primary"
                  aria-hidden
                />
                <p className="font-medium">正在重启 MyBot…</p>
                <p className="text-sm text-muted-foreground">
                  进程退出后由守护策略拉起，恢复后面板会自动重连。
                </p>
              </div>
            ) : null}
            {powerState.kind === "restart_timeout" ? (
              <div className="flex flex-col items-center gap-3 text-center">
                <p className="font-medium">重启超时</p>
                <p className="text-sm text-muted-foreground">
                  60 秒内未能重新连接，请检查服务状态或容器日志。
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPowerState({ kind: "idle" })}
                  >
                    关闭提示
                  </Button>
                  <Button size="sm" onClick={() => void onReconnect()}>
                    重试连接
                  </Button>
                </div>
              </div>
            ) : null}
            {powerState.kind === "shutdown" ? (
              <div className="flex flex-col items-center gap-3 text-center">
                <Power
                  className="h-8 w-8 text-muted-foreground"
                  aria-hidden
                />
                <p className="font-medium">MyBot 已停止</p>
                <p className="text-sm text-muted-foreground">
                  面板已与进程断开；Docker 部署下进程可能被自动拉起。
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void onReconnect()}
                >
                  尝试重新连接
                </Button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
      <Toaster
        richColors
        position="top-center"
        theme={resolvedTheme === "dark" ? "dark" : "light"}
      />
    </FormProvider>
  );
}
