/** LLM Providers 管理页：增删改具名 provider，api_key 明文（内网部署）。 */

import { useState } from "react";
import { useFormContext } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { SectionCard } from "@/components/SectionCard";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NumberField, TextField } from "@/lib/fields";
import type { LLMProviderConfig, MyBotConfigData } from "@/lib/types";

export default function ProvidersPage() {
  const { watch, setValue } = useFormContext<MyBotConfigData>();
  const [newProviderId, setNewProviderId] = useState("");
  const providers = watch("llm.providers") ?? {};
  const providerIds = Object.keys(providers);

  const addProvider = () => {
    const id = newProviderId.trim();
    if (id === "") {
      toast.error("Provider ID 不能为空");
      return;
    }
    if (id !== id.trim()) {
      toast.error("Provider ID 不能包含首尾空格");
      return;
    }
    if (id in providers) {
      toast.error(`Provider ${id} 已存在`);
      return;
    }
    const next: Record<string, LLMProviderConfig> = {
      ...providers,
      [id]: {
        api_key: null,
        base_url: null,
        max_attempts: 5,
        retry_delay_seconds: 0,
      },
    };
    setValue("llm.providers", next, { shouldDirty: true });
    setNewProviderId("");
  };

  const removeProvider = (id: string) => {
    const next = { ...providers };
    delete next[id];
    setValue("llm.providers", next, { shouldDirty: true });
  };

  return (
    <div className="space-y-6">
      <Alert>
        <AlertTitle>Provider 改动需要重启进程后生效</AlertTitle>
        <AlertDescription>
          新增或删除 provider 后，引用它的插件配置要等服务重启才会被热载接受。
        </AlertDescription>
      </Alert>

      {providerIds.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          尚未配置任何 LLM provider。
        </p>
      ) : null}

      {providerIds.map((id) => (
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
              onClick={() => removeProvider(id)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          }
        >
          <TextField
            path={`llm.providers.${id}.api_key`}
            label="API Key"
            description="留空时不发送 Authorization；内网 WebUI 明文展示"
          />
          <TextField
            path={`llm.providers.${id}.base_url`}
            label="Base URL"
            placeholder="留空使用 OpenAI 官方地址"
          />
          <NumberField
            path={`llm.providers.${id}.max_attempts`}
            label="最大尝试次数"
          />
          <NumberField
            path={`llm.providers.${id}.retry_delay_seconds`}
            label="重试间隔（秒）"
          />
        </SectionCard>
      ))}

      <SectionCard title="新增 Provider" description="ID 不能为空，也不能包含首尾空格。">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center md:col-span-2">
          <Input
            aria-label="新 Provider ID"
            placeholder="provider ID，如 deepseek"
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
    </div>
  );
}
