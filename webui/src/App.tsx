/** MyBot 配置控制台主框架：导航、加载、保存/校验、热生效反馈与冲突处理。 */

import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { FormProvider, useForm } from "react-hook-form";
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
import { ApiError, getConfig, saveConfig, validateConfig } from "@/lib/api";
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
  | { kind: "watching" }
  | { kind: "applied" }
  | { kind: "saved" };

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
  const pollGeneration = useRef(0);
  const mainContent = useRef<HTMLElement>(null);

  const methods = useForm<MyBotConfigData>({
    values: snapshot?.config ?? ({} as MyBotConfigData),
  });
  const isDirty = methods.formState.isDirty;

  const reload = useCallback(async () => {
    const fresh = await getConfig();
    setSnapshot(fresh);
    setRestartSections(fresh.meta.restart_required_sections);
    setServerIssues(fresh.issues);
  }, []);

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
    if (!snapshot) return;
    const payload = methods.getValues();
    const previousRevision = snapshot.meta.plugin_revision;
    setSaving(true);
    try {
      const result = await saveConfig(payload, snapshot.sha256);
      methods.reset(payload);
      setServerIssues([]);
      setSnapshot((previous) =>
        previous ? { ...previous, sha256: result.sha256 } : previous,
      );
      setRestartSections(result.restart_required_sections);
      if (result.restart_required_sections.length > 0) {
        toast.warning("部分改动需要重启进程后生效");
      } else {
        toast.success("配置已保存");
      }
      if (snapshot.meta.watcher_active) {
        void pollApply(previousRevision);
      } else {
        setApplyState({ kind: "saved" });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflictOpen(true);
      } else if (error instanceof ApiError && error.issues.length > 0) {
        applyIssues(error.issues);
        toast.error(`配置校验失败：${error.issues.length} 个问题`);
      } else {
        toast.error(error instanceof Error ? error.message : "保存失败");
      }
    } finally {
      setSaving(false);
    }
  }, [snapshot, methods, applyIssues, pollApply]);

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
              {applyState.kind === "watching" ? (
                <Badge variant="secondary">等待热生效…</Badge>
              ) : null}
              {applyState.kind === "applied" ? (
                <Badge className="bg-green-600 text-white">已热生效</Badge>
              ) : null}
              {applyState.kind === "saved" ? (
                <Badge variant="outline">已保存</Badge>
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
              <Button variant="outline" size="sm" onClick={onValidate}>
                校验
              </Button>
              <Button
                size="sm"
                disabled={!isDirty || saving}
                onClick={onSave}
              >
                {saving ? "保存中…" : "保存"}
              </Button>
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
            <div className="mx-auto max-w-4xl space-y-6">
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
              配置文件在你编辑期间被其他方式修改过。刷新后将丢失当前未保存的修改。
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
      <Toaster richColors position="top-center" />
    </FormProvider>
  );
}
