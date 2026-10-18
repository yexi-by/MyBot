/** MCP 服务管理页：动态服务名始终作为不透明键整体写回。 */

import { useEffect, useId, useRef, useState } from "react";
import { useFormContext } from "react-hook-form";
import { Plug, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { SectionCard } from "@/components/SectionCard";
import { SettingsGrid } from "@/components/SettingsGrid";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { countDirtyUnder } from "@/lib/dirty";
import { NumberField, SwitchField } from "@/lib/fields";
import type { MCPServerConfig, MyBotConfigData } from "@/lib/types";
import { useFieldFilter } from "@/lib/useFieldFilter";

interface EnvEntry {
  id: number;
  key: string;
  value: string;
}

function serializeEnv(env: Record<string, string> | null): string {
  return JSON.stringify(env ?? null);
}

/**
 * 环境变量和请求头共用键值编辑器，本地维护带稳定 id 的条目列表，
 * 允许空名/重名行共存；写回 RHF 时才序列化为 record（重名后者覆盖前者）。
 */
function KeyValueEntriesEditor({
  value,
  label,
  onCommit,
}: {
  value: Record<string, string> | null;
  label: string;
  onCommit: (env: Record<string, string> | null) => void;
}) {
  const nextId = useRef(0);
  const toEntries = (record: Record<string, string> | null): EnvEntry[] =>
    Object.entries(record ?? {}).map(([key, value]) => ({
      id: nextId.current++,
      key,
      value,
    }));
  const [entries, setEntries] = useState<EnvEntry[]>(() => toEntries(value));

  // 外部变更（reload / 保存后 reset）时重新同步；本地写回保持一致，不会自我覆盖。
  const serialized = serializeEnv(value);
  useEffect(() => {
    setEntries((current) => {
      const localSerialized = serializeEnv(
        current.length > 0
          ? Object.fromEntries(current.map((entry) => [entry.key, entry.value]))
          : null,
      );
      return localSerialized === serialized ? current : toEntries(value);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serialized]);

  const commit = (next: EnvEntry[]) => {
    setEntries(next);
    onCommit(
      next.length > 0
        ? Object.fromEntries(next.map((entry) => [entry.key, entry.value]))
        : null,
    );
  };

  const duplicateNames = new Set(
    entries
      .map((entry) => entry.key)
      .filter((key, index, all) => key !== "" && all.indexOf(key) !== index),
  );

  return (
    <div className="space-y-2 xl:col-span-2">
      <Label className="font-mono">{label}</Label>
      {entries.map((entry, index) => (
        <div key={entry.id} className="flex items-center gap-2">
          <Input
            className="w-2/5"
            aria-label={`${label}名称 ${index + 1}`}
            value={entry.key}
            onChange={(event) =>
              commit(
                entries.map((item, i) =>
                  i === index ? { ...item, key: event.target.value } : item,
                ),
              )
            }
          />
          <Input
            aria-label={`${label}值 ${index + 1}`}
            value={entry.value}
            onChange={(event) =>
              commit(
                entries.map((item, i) =>
                  i === index ? { ...item, value: event.target.value } : item,
                ),
              )
            }
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`删除${label} ${entry.key || index + 1}`}
            onClick={() => commit(entries.filter((_, i) => i !== index))}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      {duplicateNames.size > 0 ? (
        <p className="text-xs text-amber-600 dark:text-amber-500">
          重名{label}（{[...duplicateNames].join("、")}）保存时后者覆盖前者
        </p>
      ) : null}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() =>
          commit([...entries, { id: nextId.current++, key: "", value: "" }])
        }
      >
        <Plus className="mr-1 h-4 w-4" />添加{label}
      </Button>
    </div>
  );
}

export default function McpPage() {
  const controlPrefix = useId();
  const { watch, setValue, formState } = useFormContext<MyBotConfigData>();
  const [newServerName, setNewServerName] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const filterRef = useFieldFilter(filter);
  const servers = watch("mcp.servers") ?? {};
  const serverNames = Object.keys(servers);
  const dirtyCount = countDirtyUnder(formState.dirtyFields, ["mcp"]);

  const commit = (next: Record<string, MCPServerConfig>) => {
    setValue("mcp.servers", next, { shouldDirty: true });
  };
  const updateServer = (name: string, patch: Partial<MCPServerConfig>) => {
    commit({ ...servers, [name]: { ...servers[name], ...patch } });
  };

  const addServer = () => {
    const name = newServerName.trim();
    if (name === "") {
      toast.error("服务名不能为空");
      return;
    }
    if (name !== newServerName) {
      toast.error("服务名不能包含首尾空格");
      return;
    }
    if (Object.hasOwn(servers, name)) {
      toast.error(`服务 ${name} 已存在`);
      return;
    }
    commit({
      ...servers,
      [name]: { transport: "stdio", command: "", args: [], env: null, cwd: null, disabled: false },
    });
    setNewServerName("");
  };

  const removeServer = (name: string) => {
    const next = { ...servers };
    delete next[name];
    commit(next);
  };

  return (
    <div ref={filterRef} className="space-y-3">
      <PageHeader
        title="MCP 服务"
        description="管理 stdio 进程和 HTTP 服务；服务名、环境变量名和请求头名会原样保存。"
        notice="MCP 改动需要重启进程后生效"
        dirtyCount={dirtyCount}
        filterValue={filter}
        onFilterChange={setFilter}
      />

      {serverNames.length === 0 ? (
        <EmptyState
          icon={Plug}
          title="尚未配置 MCP 服务"
          description="在下方「新增 MCP 服务」卡片中输入服务名即可添加；MCP 工具以 mcp__{server}__{tool} 形式暴露给 AI 调用。"
        />
      ) : null}

      <SettingsGrid columns={serverNames.length + 1 >= 3 ? 3 : 2}>
        <div className="space-y-3">
        <SectionCard title="MCP 总开关" description="关闭后不再连接 MCP 服务。">
          <SwitchField
            path="mcp.enabled"
            label="启用 MCP"
            description="以 mcp__{server}__{tool} 形式暴露给 AI 工具调用"
          />
          <NumberField path="mcp.initialization_timeout_seconds" label="初始化超时（秒）" description="每个服务完成握手与工具清单加载的总等待期限" />
          <NumberField path="mcp.call_timeout_seconds" label="工具调用超时（秒）" description="超时会向 AI 返回可恢复错误" />
        </SectionCard>

        <SectionCard title="新增 MCP 服务">
          <div className="flex flex-col gap-2 xl:col-span-2">
            <Input
              aria-label="新 MCP 服务名"
              placeholder="服务名，如 vendor.tool"
              value={newServerName}
              onChange={(event) => setNewServerName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addServer();
                }
              }}
            />
            <Button type="button" onClick={addServer}>
              <Plus className="mr-1 h-4 w-4" />添加
            </Button>
          </div>
        </SectionCard>
        </div>

        {serverNames.map((name) => {
          const server = servers[name];
          const args = server.args ?? [];
          const transport = server.transport ?? "stdio";
          return (
            <SectionCard
              key={name}
              title={name}
              actions={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`删除 MCP 服务 ${name}`}
                  onClick={() => setDeleteTarget(name)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              }
            >
              <div className="space-y-1.5 xl:col-span-2">
                <Label className="font-mono" htmlFor={`${controlPrefix}-${name}-transport`}>连接方式</Label>
                <Select
                  value={transport}
                  onValueChange={(value) => {
                    if ((value !== "stdio" && value !== "streamable_http") || value === transport) return;
                    commit({
                      ...servers,
                      [name]: value === "streamable_http"
                        ? { transport: value, url: "", headers: null, disabled: server.disabled }
                        : { transport: value, command: "", args: [], env: null, cwd: null, disabled: server.disabled },
                    });
                  }}
                >
                  <SelectTrigger id={`${controlPrefix}-${name}-transport`} className="w-full">
                    <SelectValue>{transport === "stdio" ? "stdio 本地进程" : "Streamable HTTP"}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="stdio">stdio 本地进程</SelectItem>
                    <SelectItem value="streamable_http">Streamable HTTP</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {transport === "streamable_http" ? (
                <>
                  <div className="space-y-1.5 xl:col-span-2">
                    <Label className="font-mono" htmlFor={`${controlPrefix}-${name}-url`}>服务地址</Label>
                    <Input
                      id={`${controlPrefix}-${name}-url`}
                      type="url"
                      value={server.url ?? ""}
                      placeholder="https://mcp.firecrawl.dev/v2/mcp"
                      onChange={(event) => updateServer(name, { url: event.target.value })}
                    />
                  </div>
                  <KeyValueEntriesEditor
                    key={`${name}-headers`}
                    label="请求头"
                    value={server.headers ?? null}
                    onCommit={(headers) => updateServer(name, { headers })}
                  />
                </>
              ) : (
                <>
                  <div className="space-y-1.5">
                    <Label className="font-mono" htmlFor={`${controlPrefix}-${name}-command`}>启动命令</Label>
                    <Input
                      id={`${controlPrefix}-${name}-command`}
                      value={server.command ?? ""}
                      placeholder="如 npx / uvx"
                      onChange={(event) => updateServer(name, { command: event.target.value })}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="font-mono" htmlFor={`${controlPrefix}-${name}-cwd`}>工作目录</Label>
                    <Input
                      id={`${controlPrefix}-${name}-cwd`}
                      value={server.cwd ?? ""}
                      placeholder="留空使用进程当前目录"
                      onChange={(event) => updateServer(name, { cwd: event.target.value || null })}
                    />
                  </div>
                  <div className="space-y-2 xl:col-span-2">
                    <Label className="font-mono">命令参数</Label>
                    {args.map((argument, index) => (
                      <div key={index} className="flex items-center gap-2">
                        <Input
                          aria-label={`命令参数 ${index + 1}`}
                          value={argument}
                          onChange={(event) => {
                            const next = [...args];
                            next[index] = event.target.value;
                            updateServer(name, { args: next });
                          }}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`删除命令参数 ${index + 1}`}
                          onClick={() => updateServer(name, { args: args.filter((_, i) => i !== index) })}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => updateServer(name, { args: [...args, ""] })}
                    >
                      <Plus className="mr-1 h-4 w-4" />添加参数
                    </Button>
                  </div>
                  <KeyValueEntriesEditor
                    key={`${name}-env`}
                    label="环境变量"
                    value={server.env ?? null}
                    onCommit={(env) => updateServer(name, { env })}
                  />
                </>
              )}
              <div className="space-y-1.5">
                <Label htmlFor={`${controlPrefix}-${name}-disabled`}>禁用此服务</Label>
                <Switch
                  id={`${controlPrefix}-${name}-disabled`}
                  checked={Boolean(server.disabled)}
                  onCheckedChange={(checked) =>
                    updateServer(name, { disabled: checked })
                  }
                />
              </div>
            </SectionCard>
          );
        })}

      </SettingsGrid>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title={`删除 MCP 服务 ${deleteTarget ?? ""}？`}
        description="删除后该服务的连接配置将随自动保存一并移除，且无法恢复。"
        confirmLabel="删除服务"
        destructive
        onConfirm={() => {
          if (deleteTarget !== null) removeServer(deleteTarget);
        }}
      />
    </div>
  );
}
