/** 模型引用字段组：provider 下拉（来自已配置 providers）+ 模型名 + 可选图片能力开关。 */

import { useFormContext } from "react-hook-form";

import { SelectField, SwitchField, TextField } from "@/lib/fields";
import type { MyBotConfigData } from "@/lib/types";

interface ModelRefFieldProps {
  path: string;
  /** 是否展示 supports_images 开关（ChatModelRef）。 */
  withSupportsImages?: boolean;
}

export function ModelRefField({ path, withSupportsImages }: ModelRefFieldProps) {
  const { watch } = useFormContext<MyBotConfigData>();
  const providerIds = Object.keys(watch("llm.providers") ?? {});
  return (
    <>
      <SelectField
        path={`${path}.provider`}
        label="Provider"
        description="引用模型 Providers 页配置的服务"
        options={providerIds.map((id) => ({ value: id, label: id }))}
        placeholder="选择 provider"
      />
      <TextField
        path={`${path}.name`}
        label="模型名"
        placeholder="如 deepseek-chat"
      />
      {withSupportsImages ? (
        <SwitchField
          path={`${path}.supports_images`}
          label="支持图片输入"
          description="开启后不再使用视觉描述工具；注意与视觉模型配置互斥"
        />
      ) : null}
    </>
  );
}
