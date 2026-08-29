/** 模型引用字段组：provider 下拉 + 模型名（自动拉取 provider 模型列表供筛选选择，仍可手输）+ 可选图片能力开关。 */

import { useCallback, useEffect, useId, useState } from "react";
import { useFormContext } from "react-hook-form";
import { RotateCw, TriangleAlert } from "lucide-react";

import { getProviderModels } from "@/lib/api";
import { FieldShell, SelectField, SwitchField } from "@/lib/fields";
import { Input } from "@/components/ui/input";
import type { MyBotConfigData } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 从当前配置中收集已使用过的模型名，作为拉取失败时的 datalist 兜底。 */
function collectModelNames(config: MyBotConfigData): string[] {
  const names = new Set<string>();
  const refs = [
    config.plugins?.ai_group_chat?.model,
    config.plugins?.ai_group_chat?.vision?.model,
    config.plugins?.image_generate?.model,
  ];
  for (const ref of refs) {
    if (ref?.name) names.add(ref.name);
  }
  return [...names];
}

interface ModelRefFieldProps {
  path: string;
  /** 是否展示 supports_images 开关（ChatModelRef）。 */
  withSupportsImages?: boolean;
  onSupportsImagesChange?: (checked: boolean) => void;
}

export function ModelRefField({
  path,
  withSupportsImages,
  onSupportsImagesChange,
}: ModelRefFieldProps) {
  const { watch, getValues, register } = useFormContext<MyBotConfigData>();
  const providerIds = Object.keys(watch("llm.providers") ?? {});
  const provider = watch(`${path}.provider` as never) as unknown as
    | string
    | undefined;
  const datalistId = useId();
  const nameControlId = useId();
  const [models, setModels] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const fetchModels = useCallback(() => {
    if (!provider) {
      setModels(null);
      setFetchError(null);
      return;
    }
    setLoading(true);
    setFetchError(null);
    getProviderModels(provider)
      .then((result) => setModels(result.models))
      .catch((error: unknown) => {
        setModels(null);
        setFetchError(
          error instanceof Error ? error.message : "拉取模型列表失败",
        );
      })
      .finally(() => setLoading(false));
  }, [provider]);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  const suggestions = models ?? collectModelNames(getValues());

  return (
    <>
      <SelectField
        path={`${path}.provider`}
        label="Provider"
        description="引用模型 Providers 页配置的服务"
        options={providerIds.map((id) => ({ value: id, label: id }))}
        placeholder="选择 provider"
      />
      <FieldShell
        path={`${path}.name`}
        label={
          <span className="inline-flex items-center gap-1">
            模型名
            {provider ? (
              <button
                type="button"
                aria-label="重新拉取模型列表"
                title="重新拉取模型列表"
                onClick={fetchModels}
                className="cursor-pointer text-muted-foreground hover:text-foreground"
              >
                <RotateCw
                  className={cn("size-3", loading && "animate-spin")}
                  aria-hidden
                />
              </button>
            ) : null}
            {fetchError ? (
              <span title={fetchError} aria-label="模型列表拉取失败">
                <TriangleAlert
                  className="size-3 text-amber-600 dark:text-amber-500"
                  aria-hidden
                />
              </span>
            ) : null}
          </span>
        }
        description={
          models
            ? `${provider} 返回 ${models.length} 个模型，输入可筛选，也可直接填写`
            : fetchError
              ? "模型列表拉取失败，可手动输入模型名"
              : "选择 provider 后自动拉取可选模型，也可手动输入"
        }
        controlId={nameControlId}
      >
        <Input
          id={nameControlId}
          list={datalistId}
          placeholder="如 deepseek-chat"
          {...register(`${path}.name` as never, {
            setValueAs: (value: unknown) => (value === "" ? undefined : value),
          })}
        />
        <datalist id={datalistId}>
          {suggestions.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </FieldShell>
      {withSupportsImages ? (
        <SwitchField
          path={`${path}.supports_images`}
          label="支持图片输入"
          description="只声明模型能力；实际使用 direct 还是 vision 由图片交付方式决定"
          onCheckedChange={onSupportsImagesChange}
        />
      ) : null}
    </>
  );
}
