/** LLM Providers 管理页：动态 ID 始终作为不透明键整体写回。 */

import { useState } from "react";
import { useFormContext } from "react-hook-form";
import { Cable, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { SectionCard } from "@/components/SectionCard";
import { SettingsGrid } from "@/components/SettingsGrid";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { countDirtyUnder } from "@/lib/dirty";
import type { LLMProviderConfig, MyBotConfigData } from "@/lib/types";
import { useFieldFilter } from "@/lib/useFieldFilter";

function numberInput(value: string): number | undefined {
  return value === "" ? undefined : Number(value);
}

export default function ProvidersPage() {
  const { watch, setValue, formState } = useFormContext<MyBotConfigData>();
  const [newProviderId, setNewProviderId] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const filterRef = useFieldFilter(filter);
  const providers = watch("llm.providers") ?? {};
  const providerIds = Object.keys(providers);
  const dirtyCount = countDirtyUnder(formState.dirtyFields, ["llm"]);

  const commit = (next: Record<string, LLMProviderConfig>) => {
    setValue("llm.providers", next, { shouldDirty: true });
  };
  const updateProvider = (
    id: string,
    patch: Partial<LLMProviderConfig>,
  ) => {
    commit({ ...providers, [id]: { ...providers[id], ...patch } });
  };

  const addProvider = () => {
    const id = newProviderId.trim();
    if (id === "") {
      toast.error("Provider ID 不能为空");
      return;
    }
    if (id !== newProviderId) {
      toast.error("Provider ID 不能包含首尾空格");
      return;
    }
    if (Object.hasOwn(providers, id)) {
      toast.error(`Provider ${id} 已存在`);
      return;
    }
    commit({
      ...providers,
      [id]: {
        api_key: null,
        base_url: null,
        proxy: null,
        inherit_network_proxy: true,
        timeout_seconds: 0,
        max_attempts: 5,
        retry_delay_seconds: 0,
        retry_max_delay_seconds: 0,
      },
    });
    setNewProviderId("");
  };

  const removeProvider = (id: string) => {
    const next = { ...providers };
    delete next[id];
    commit(next);
  };

  return (
    <div ref={filterRef} className="space-y-3">
      <PageHeader
        title="模型 Providers"
        description="OpenAI 兼容服务接入；Provider ID 可以包含点号，界面会保留完整键名。"
        notice="Provider 改动需要重启进程后生效"
        dirtyCount={dirtyCount}
        filterValue={filter}
        onFilterChange={setFilter}
      />

      {providerIds.length === 0 ? (
        <EmptyState
          icon={Cable}
          title="尚未配置 LLM Provider"
          description="在下方「新增 Provider」卡片中输入 ID 即可添加；插件的模型引用从这里选择服务。"
        />
      ) : null}

      <SettingsGrid columns={providerIds.length + 1 >= 3 ? 3 : 2}>
        {providerIds.map((id) => {
          const provider = providers[id];
          return (
            <SectionCard
              key={id}
              title={id}
              description="OpenAI 兼容服务"
              actions={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`删除 Provider ${id}`}
                  onClick={() => setDeleteTarget(id)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              }
            >
              <div className="space-y-1.5">
                <Label>API Key</Label>
                <Input
                  value={provider.api_key ?? ""}
                  onChange={(event) =>
                    updateProvider(id, { api_key: event.target.value || null })
                  }
                />
                <p className="text-xs text-muted-foreground">
                  留空时不发送 Authorization；内网 WebUI 明文展示
                </p>
              </div>
              <div className="space-y-1.5">
                <Label>Base URL</Label>
                <Input
                  value={provider.base_url ?? ""}
                  onChange={(event) =>
                    updateProvider(id, { base_url: event.target.value || null })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label>Provider 代理</Label>
                <Input
                  value={provider.proxy ?? ""}
                  placeholder="留空时按继承开关决定"
                  onChange={(event) =>
                    updateProvider(id, { proxy: event.target.value || null })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label>继承全局代理</Label>
                <Switch
                  checked={Boolean(provider.inherit_network_proxy)}
                  onCheckedChange={(checked) =>
                    updateProvider(id, { inherit_network_proxy: checked })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label>请求超时（秒）</Label>
                <Input
                  type="number"
                  step="any"
                  value={provider.timeout_seconds ?? ""}
                  onChange={(event) =>
                    updateProvider(id, {
                      timeout_seconds: numberInput(event.target.value),
                    })
                  }
                />
                <p className="text-xs text-muted-foreground">0 表示使用全局网络超时</p>
              </div>
              <div className="space-y-1.5">
                <Label>最大尝试次数</Label>
                <Input
                  type="number"
                  value={provider.max_attempts ?? ""}
                  onChange={(event) =>
                    updateProvider(id, {
                      max_attempts: numberInput(event.target.value),
                    })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label>初始重试间隔（秒）</Label>
                <Input
                  type="number"
                  step="any"
                  value={provider.retry_delay_seconds ?? ""}
                  onChange={(event) =>
                    updateProvider(id, {
                      retry_delay_seconds: numberInput(event.target.value),
                    })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label>最大重试间隔（秒）</Label>
                <Input
                  type="number"
                  step="any"
                  value={provider.retry_max_delay_seconds ?? ""}
                  onChange={(event) =>
                    updateProvider(id, {
                      retry_max_delay_seconds: numberInput(event.target.value),
                    })
                  }
                />
                <p className="text-xs text-muted-foreground">0 表示不限制指数退避上限</p>
              </div>
            </SectionCard>
          );
        })}

        <SectionCard title="新增 Provider" description="ID 不能为空，也不能包含首尾空格。">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center xl:col-span-2">
            <Input
              aria-label="新 Provider ID"
              placeholder="provider ID，如 deep.seek"
              value={newProviderId}
              onChange={(event) => setNewProviderId(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addProvider();
                }
              }}
            />
            <Button type="button" className="sm:w-auto" onClick={addProvider}>
              <Plus className="mr-1 h-4 w-4" />
              添加
            </Button>
          </div>
        </SectionCard>
      </SettingsGrid>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title={`删除 Provider ${deleteTarget ?? ""}？`}
        description="删除后该 Provider 的 API Key 与连接参数将随自动保存一并移除，且无法恢复；引用它的模型配置需要重新选择服务。"
        confirmLabel="删除 Provider"
        destructive
        onConfirm={() => {
          if (deleteTarget !== null) removeProvider(deleteTarget);
        }}
      />
    </div>
  );
}
