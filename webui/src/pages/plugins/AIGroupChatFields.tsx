/** AI 群聊插件配置表单：模型、视觉、图片、行为开关与群列表。 */

import { useFieldArray, useFormContext } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";

import { ModelRefField } from "@/components/ModelRefField";
import { SectionCard } from "@/components/SectionCard";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  NumberField,
  SwitchField,
  TextField,
  TextareaField,
} from "@/lib/fields";
import type { MyBotConfigData } from "@/lib/types";

const VISION_BASE = "plugins.ai_group_chat.vision" as const;
const GROUPS_BASE = "plugins.ai_group_chat.groups" as const;

function VisionSection() {
  const { watch, setValue } = useFormContext<MyBotConfigData>();
  const vision = watch(VISION_BASE);
  const visionEnabled = vision != null;
  const supportsImages = Boolean(
    watch("plugins.ai_group_chat.model.supports_images"),
  );

  return (
    <SectionCard
      title="视觉模型"
      description="主模型不支持图片输入时，用视觉模型生成图片事实描述。"
      actions={
        <Switch
          aria-label="启用视觉模型"
          checked={visionEnabled}
          disabled={supportsImages}
          onCheckedChange={(checked) => {
            if (checked) {
              setValue(
                VISION_BASE,
                {
                  model: { provider: "", name: "" },
                  system_prompt_file: "ai_group_chat/prompts/vision/system.md",
                  user_prompt_file: "ai_group_chat/prompts/vision/user.md",
                },
                { shouldDirty: true },
              );
            } else {
              setValue(VISION_BASE, null, { shouldDirty: true });
            }
          }}
        />
      }
    >
      {supportsImages ? (
        <div className="md:col-span-2">
          <Alert>
            <AlertTitle>主模型已支持图片</AlertTitle>
            <AlertDescription>
              supports_images 开启时不能配置视觉模型，两者互斥。
            </AlertDescription>
          </Alert>
        </div>
      ) : null}
      {visionEnabled ? (
        <>
          <ModelRefField path={`${VISION_BASE}.model`} />
          <TextField
            path={`${VISION_BASE}.system_prompt_file`}
            label="系统提示词文件"
            placeholder="相对 config 目录的路径"
          />
          <TextField
            path={`${VISION_BASE}.user_prompt_file`}
            label="用户提示词文件"
            placeholder="相对 config 目录的路径"
          />
          <NumberField
            path={`${VISION_BASE}.max_attempts`}
            label="最大尝试次数"
            placeholder="默认 5"
          />
          <NumberField
            path={`${VISION_BASE}.retry_delay_seconds`}
            label="重试间隔（秒）"
            placeholder="默认 0.25"
          />
          <SwitchField
            path={`${VISION_BASE}.retain_descriptions`}
            label="保留图片描述"
            description="把视觉描述写入长期上下文"
          />
        </>
      ) : (
        <p className="text-sm text-muted-foreground md:col-span-2">
          未配置视觉模型；主模型不支持图片时必须启用。
        </p>
      )}
    </SectionCard>
  );
}

function GroupsSection() {
  const { control } = useFormContext<MyBotConfigData>();
  const { fields, append, remove } = useFieldArray({
    control,
    name: GROUPS_BASE,
  });

  return (
    <>
      {fields.map((field, index) => (
        <SectionCard
          key={field.id}
          title={`群配置 ${index + 1}`}
          actions={
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`删除群配置 ${index + 1}`}
              onClick={() => remove(index)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          }
        >
          <TextField
            path={`${GROUPS_BASE}.${index}.id`}
            label="群号"
            placeholder="QQ 群号"
          />
          <NumberField
            path={`${GROUPS_BASE}.${index}.max_context_tokens`}
            label="上下文 Token 上限"
            placeholder="如 64000"
          />
          <TextField
            path={`${GROUPS_BASE}.${index}.system_prompt_file`}
            label="角色提示词文件"
            placeholder="ai_group_chat/prompts/roles/default.md"
          />
          <TextField
            path={`${GROUPS_BASE}.${index}.knowledge_base_file`}
            label="知识库文件"
            placeholder="留空不使用知识库"
          />
        </SectionCard>
      ))}
      <Button
        type="button"
        variant="outline"
        onClick={() =>
          append({
            id: "",
            system_prompt_file: "ai_group_chat/prompts/roles/default.md",
            knowledge_base_file: null,
            max_context_tokens: 64000,
          })
        }
      >
        <Plus className="mr-1 h-4 w-4" />
        添加群配置
      </Button>
    </>
  );
}

export default function AIGroupChatFields() {
  return (
    <>
      <SectionCard title="主模型" description="群聊对话使用的聊天模型。">
        <ModelRefField path="plugins.ai_group_chat.model" withSupportsImages />
      </SectionCard>

      <VisionSection />

      <SectionCard title="图片处理" description="群聊图片读取与合并转发限制。">
        <NumberField
          path="plugins.ai_group_chat.images.max_per_turn"
          label="每轮最多图片数"
          placeholder="默认 20"
        />
        <NumberField
          path="plugins.ai_group_chat.images.fetch_concurrency"
          label="取图并发数"
          placeholder="默认 16"
        />
        <NumberField
          path="plugins.ai_group_chat.images.download_timeout_seconds"
          label="下载超时（秒）"
          placeholder="默认 20"
        />
        <SwitchField
          path="plugins.ai_group_chat.images.forward_tool_enabled"
          label="启用合并转发工具"
        />
        <NumberField
          path="plugins.ai_group_chat.images.forward_max_per_call"
          label="单次转发上限"
          placeholder="默认 20"
        />
        <NumberField
          path="plugins.ai_group_chat.images.forward_max_per_turn"
          label="单轮转发上限"
          placeholder="默认 50"
        />
      </SectionCard>

      <SectionCard title="对话行为" description="工具循环、上下文与回复控制。">
        <NumberField
          path="plugins.ai_group_chat.max_tool_rounds"
          label="最大工具轮数"
          placeholder="默认 16"
        />
        <NumberField
          path="plugins.ai_group_chat.token_safety_factor"
          label="Token 安全系数"
          placeholder="默认 1.05"
        />
        <NumberField
          path="plugins.ai_group_chat.max_reply_chars"
          label="回复最大字符数"
          placeholder="默认 1000"
        />
        <TextField
          path="plugins.ai_group_chat.extra_requirements_file"
          label="通用要求文件"
          placeholder="ai_group_chat/prompts/extra_requirements.md"
        />
        <div className="md:col-span-2">
          <TextareaField
            path="plugins.ai_group_chat.context_compression_notice"
            label="上下文压缩提示语"
            rows={2}
          />
        </div>
        <SwitchField
          path="plugins.ai_group_chat.show_reasoning"
          label="展示推理过程"
        />
        <SwitchField
          path="plugins.ai_group_chat.retain_reasoning"
          label="保留推理到上下文"
        />
        <SwitchField
          path="plugins.ai_group_chat.debug_dump_messages"
          label="调试消息转储"
          description="写入 logs/ai_group_chat_debug/"
        />
        <SwitchField
          path="plugins.ai_group_chat.allow_mention_all"
          label="允许 @全体"
        />
        <SwitchField
          path="plugins.ai_group_chat.retain_tool_results"
          label="工具结果写入长期上下文"
          description="默认关闭，避免上下文膨胀"
        />
      </SectionCard>

      <div className="space-y-4">
        <h3 className="text-base font-medium">群列表</h3>
        <GroupsSection />
      </div>
    </>
  );
}
