/** AI 群聊插件配置表单：模型、视觉、图片、行为开关与群列表。 */

import { useState } from "react";
import { useFieldArray, useFormContext } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ModelRefField } from "@/components/ModelRefField";
import { SectionCard } from "@/components/SectionCard";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  NumberField,
  SelectField,
  StringListField,
  SwitchField,
  TextField,
  TextareaField,
} from "@/lib/fields";
import type { AIVisionConfig, MyBotConfigData } from "@/lib/types";

const VISION_BASE = "plugins.ai_group_chat.vision" as const;
const GROUPS_BASE = "plugins.ai_group_chat.groups" as const;

function defaultVision(provider: string): AIVisionConfig {
  return {
    model: { provider, name: "" },
    system_prompt_file: "ai_group_chat/prompts/vision/system.md",
    user_prompt_file: "ai_group_chat/prompts/vision/user.md",
    max_attempts: 5,
    retry_delay_seconds: 0.25,
    retry_max_delay_seconds: 0,
    retain_descriptions: true,
  };
}

function VisionSection() {
  const { watch, setValue } = useFormContext<MyBotConfigData>();
  const vision = watch(VISION_BASE);
  const visionEnabled = vision != null;
  const supportsImages = Boolean(
    watch("plugins.ai_group_chat.model.supports_images"),
  );
  const provider = watch("plugins.ai_group_chat.model.provider") ?? "";
  const deliveryMode = watch("plugins.ai_group_chat.images.delivery_mode");
  const oversizeBehavior = watch(
    "plugins.ai_group_chat.images.oversize_behavior",
  );

  return (
    <SectionCard
      title="视觉模型"
      description="只有 delivery_mode=vision 或 oversize_behavior=describe 时才会调用。"
      actions={
        <Switch
          aria-label="启用视觉模型"
          checked={visionEnabled}
          disabled={visionEnabled && !supportsImages}
          onCheckedChange={(checked) => {
            if (checked) {
              setValue(VISION_BASE, defaultVision(provider), { shouldDirty: true });
              setValue("plugins.ai_group_chat.images.delivery_mode", "vision", {
                shouldDirty: true,
              });
              setValue("plugins.ai_group_chat.images.oversize_behavior", "skip", {
                shouldDirty: true,
              });
              setValue("plugins.ai_group_chat.images.retain_images", false, {
                shouldDirty: true,
              });
            } else {
              if (deliveryMode === "vision") {
                setValue("plugins.ai_group_chat.images.delivery_mode", "direct", {
                  shouldDirty: true,
                });
              }
              if (oversizeBehavior === "describe") {
                setValue("plugins.ai_group_chat.images.oversize_behavior", "skip", {
                  shouldDirty: true,
                });
              }
              setValue(VISION_BASE, null, { shouldDirty: true });
            }
          }}
        />
      }
    >
      {visionEnabled && !supportsImages ? (
        <div className="xl:col-span-2">
          <Alert>
            <AlertTitle>视觉模型当前是必需项</AlertTitle>
            <AlertDescription>
              主模型没有图片能力。请先把主模型标记为支持图片，才能关闭视觉模型。
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
            placeholder="例如 5"
          />
          <NumberField
            path={`${VISION_BASE}.retry_delay_seconds`}
            label="重试间隔（秒）"
            placeholder="例如 0.25"
          />
          <NumberField
            path={`${VISION_BASE}.retry_max_delay_seconds`}
            label="最大重试间隔（秒）"
            description="0 表示不限制指数退避上限"
          />
          <SwitchField
            path={`${VISION_BASE}.retain_descriptions`}
            label="保留图片描述"
            description="把视觉描述写入长期上下文"
          />
        </>
      ) : (
        <p className="text-sm text-muted-foreground xl:col-span-2">
          未配置视觉模型；direct 模式下图片只会交给主模型。
        </p>
      )}
    </SectionCard>
  );
}

function GroupsSection() {
  const { control, watch } = useFormContext<MyBotConfigData>();
  const { fields, append, remove } = useFieldArray({
    control,
    name: GROUPS_BASE,
  });
  const [deleteIndex, setDeleteIndex] = useState<number | null>(null);

  return (
    <>
      {fields.map((field, index) => {
        const groupId = watch(`${GROUPS_BASE}.${index}.id`);
        const title =
          typeof groupId === "string" && groupId.trim() !== ""
            ? `群 ${groupId.trim()}`
            : `群配置 ${index + 1}`;
        return (
          <SectionCard
            key={field.id}
            title={title}
            actions={
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`删除${title}`}
                onClick={() => setDeleteIndex(index)}
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
        );
      })}
      <Button
        type="button"
        variant="outline"
        className="min-h-32 h-full w-full border-dashed text-muted-foreground hover:text-foreground"
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
      <ConfirmDialog
        open={deleteIndex !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteIndex(null);
        }}
        title={`删除群配置 ${deleteIndex !== null ? deleteIndex + 1 : ""}？`}
        description="删除后该群的模型上下文与提示词引用将随自动保存一并移除，且无法恢复。"
        confirmLabel="删除群配置"
        destructive
        onConfirm={() => {
          if (deleteIndex !== null) remove(deleteIndex);
        }}
      />
    </>
  );
}

export default function AIGroupChatFields() {
  const { getValues, setValue, watch } = useFormContext<MyBotConfigData>();
  const deliveryMode = watch("plugins.ai_group_chat.images.delivery_mode");
  return (
    <>
      <div className="col-span-full grid grid-cols-1 items-start gap-3 md:grid-cols-2 2xl:grid-cols-3">
        <div className="space-y-3">
      <SectionCard title="主模型" description="群聊对话使用的聊天模型。">
        <ModelRefField
          path="plugins.ai_group_chat.model"
          withSupportsImages
          onSupportsImagesChange={(checked) => {
            if (checked) {
              if (getValues(VISION_BASE) == null) {
                setValue("plugins.ai_group_chat.images.delivery_mode", "direct", {
                  shouldDirty: true,
                });
              }
              return;
            }
            if (getValues(VISION_BASE) == null) {
              setValue(
                VISION_BASE,
                defaultVision(
                  getValues("plugins.ai_group_chat.model.provider") ?? "",
                ),
                { shouldDirty: true },
              );
            }
            setValue("plugins.ai_group_chat.images.delivery_mode", "vision", {
              shouldDirty: true,
            });
            setValue("plugins.ai_group_chat.images.oversize_behavior", "skip", {
              shouldDirty: true,
            });
            setValue("plugins.ai_group_chat.images.retain_images", false, {
              shouldDirty: true,
            });
          }}
        />
      </SectionCard>

      <SectionCard title="历史工具" description="群历史分页和锚点默认数量。">
        <NumberField
          path="plugins.ai_group_chat.history.default_limit"
          label="默认单页条数"
        />
        <NumberField
          path="plugins.ai_group_chat.history.max_per_call"
          label="单页最大条数"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.ai_group_chat.history.default_before_count"
          label="锚点前默认条数"
        />
        <NumberField
          path="plugins.ai_group_chat.history.default_after_count"
          label="锚点后默认条数"
        />
      </SectionCard>

      <SectionCard title="群文件工具" description="群文件列表工具的返回数量。">
        <NumberField
          path="plugins.ai_group_chat.files.default_count"
          label="默认文件数量"
        />
        <NumberField
          path="plugins.ai_group_chat.files.max_per_call"
          label="单次最大文件数量"
          description="0 表示不限"
        />
      </SectionCard>
        </div>
        <div className="space-y-3">
      <VisionSection />

      <SectionCard title="Token 估算" description="按当前模型调整无 tokenizer 时的估算参数。">
        <NumberField path="plugins.ai_group_chat.token_estimator.request_overhead_tokens" label="请求固定 Token" />
        <NumberField path="plugins.ai_group_chat.token_estimator.message_overhead_tokens" label="每条消息固定 Token" />
        <NumberField path="plugins.ai_group_chat.token_estimator.tool_call_overhead_tokens" label="每次工具调用固定 Token" />
        <NumberField path="plugins.ai_group_chat.token_estimator.image_tokens" label="每张图片 Token" />
        <NumberField path="plugins.ai_group_chat.token_estimator.ascii_tokens_per_character" label="ASCII 每字符 Token" />
        <NumberField path="plugins.ai_group_chat.token_estimator.non_ascii_tokens_per_character" label="非 ASCII 每字符 Token" />
      </SectionCard>
        </div>
        <div className="space-y-3">
      <SectionCard title="对话行为" description="工具循环、上下文与回复控制。">
        <NumberField
          path="plugins.ai_group_chat.max_tool_rounds"
          label="最大工具轮数"
          placeholder="例如 16"
        />
        <NumberField
          path="plugins.ai_group_chat.token_safety_factor"
          label="Token 安全系数"
          placeholder="例如 1.05"
        />
        <NumberField
          path="plugins.ai_group_chat.forward_reply_threshold_chars"
          label="转合并转发字符阈值"
          description="0 表示所有非空回复都使用合并转发"
          placeholder="例如 1000"
        />
        <TextField
          path="plugins.ai_group_chat.extra_requirements_file"
          label="通用要求文件"
          placeholder="ai_group_chat/prompts/extra_requirements.md"
        />
        <div className="xl:col-span-2">
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
          description="把长期上下文增量写入下方指定目录"
        />
        <TextField
          path="plugins.ai_group_chat.debug_dump_directory"
          label="调试转储目录"
        />
        <SwitchField
          path="plugins.ai_group_chat.allow_mention_all"
          label="允许 @全体"
        />
        <SelectField
          path="plugins.ai_group_chat.tool_result_retention"
          label="工具结果长期保存"
          options={[
            { value: "off", label: "off（不保存）" },
            { value: "summary", label: "summary（名称与状态）" },
            { value: "full", label: "full（参数与完整结果）" },
          ]}
        />
      </SectionCard>

      <SectionCard title="消息格式化" description="字符和条目上限为 0 时不限；深度为 -1 时不限。">
        <NumberField path="plugins.ai_group_chat.formatting.field_text_limit" label="普通字段字符上限" />
        <NumberField path="plugins.ai_group_chat.formatting.json_text_limit" label="JSON 字符上限" />
        <NumberField path="plugins.ai_group_chat.formatting.markdown_text_limit" label="Markdown 字符上限" />
        <NumberField path="plugins.ai_group_chat.formatting.forward_max_items" label="内嵌转发条目上限" />
        <NumberField path="plugins.ai_group_chat.formatting.forward_max_depth" label="转发展开深度" />
        <NumberField path="plugins.ai_group_chat.formatting.nested_text_search_max_depth" label="卡片文本搜索深度" />
      </SectionCard>
        </div>
      </div>

      <SectionCard
        title="图片处理"
        description="群聊图片读取与合并转发限制。"
        className="col-span-full"
        cols={3}
      >
        <SelectField
          path="plugins.ai_group_chat.images.delivery_mode"
          label="图片交付方式"
          description="direct 原图直达主模型；vision 先由视觉模型生成描述"
          options={[
            { value: "direct", label: "direct（原图直达主模型）" },
            { value: "vision", label: "vision（生成图片描述）" },
          ]}
          onValueChange={(value) => {
            if (value === "vision") {
              if (getValues(VISION_BASE) == null) {
                setValue(
                  VISION_BASE,
                  defaultVision(
                    getValues("plugins.ai_group_chat.model.provider") ?? "",
                  ),
                  { shouldDirty: true },
                );
              }
              setValue("plugins.ai_group_chat.images.retain_images", false, {
                shouldDirty: true,
              });
              if (
                getValues("plugins.ai_group_chat.images.oversize_behavior") ===
                "describe"
              ) {
                setValue(
                  "plugins.ai_group_chat.images.oversize_behavior",
                  "skip",
                  { shouldDirty: true },
                );
              }
            } else {
              if (
                !getValues("plugins.ai_group_chat.model.supports_images")
              ) {
                setValue(
                  "plugins.ai_group_chat.model.supports_images",
                  true,
                  { shouldDirty: true },
                );
              }
              if (
                getValues(VISION_BASE) != null &&
                getValues("plugins.ai_group_chat.images.oversize_behavior") !==
                  "describe"
              ) {
                setValue(
                  "plugins.ai_group_chat.images.oversize_behavior",
                  "describe",
                  { shouldDirty: true },
                );
              }
            }
          }}
        />
        <NumberField
          path="plugins.ai_group_chat.images.max_per_turn"
          label="每轮最多图片数"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.ai_group_chat.images.max_image_bytes"
          label="单图最大字节"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.ai_group_chat.images.max_total_bytes_per_request"
          label="单次请求图片总字节"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.ai_group_chat.images.max_width"
          label="图片最大宽度"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.ai_group_chat.images.max_height"
          label="图片最大高度"
          description="0 表示不限"
        />
        <div className="xl:col-span-3">
          <StringListField
            path="plugins.ai_group_chat.images.allowed_mime_types"
            label="允许的图片 MIME 类型"
            description="留空接受读取器能够识别并交给 provider 的格式"
            placeholder="如 image/png"
            addLabel="添加 MIME 类型"
          />
        </div>
        <SelectField
          path="plugins.ai_group_chat.images.oversize_behavior"
          label="图片超限处理"
          options={[
            { value: "skip", label: "跳过并报告给模型" },
            { value: "error", label: "立即报错" },
            { value: "describe", label: "交给已配置的视觉模型描述" },
          ]}
          onValueChange={(value) => {
            if (value !== "describe") {
              if (
                getValues("plugins.ai_group_chat.images.delivery_mode") ===
                  "direct" &&
                getValues(VISION_BASE) != null
              ) {
                setValue(VISION_BASE, null, { shouldDirty: true });
              }
              return;
            }
            if (getValues(VISION_BASE) == null) {
              setValue(
                VISION_BASE,
                defaultVision(
                  getValues("plugins.ai_group_chat.model.provider") ?? "",
                ),
                { shouldDirty: true },
              );
            }
            setValue("plugins.ai_group_chat.model.supports_images", true, {
              shouldDirty: true,
            });
            setValue("plugins.ai_group_chat.images.delivery_mode", "direct", {
              shouldDirty: true,
            });
          }}
        />
        <SelectField
          path="plugins.ai_group_chat.images.image_detail"
          label="多模态 detail"
          options={[
            { value: "omit", label: "omit（不发送字段）" },
            { value: "auto", label: "auto" },
            { value: "low", label: "low" },
            { value: "high", label: "high" },
          ]}
        />
        <SwitchField
          path="plugins.ai_group_chat.images.retain_images"
          label="图片跨轮保留"
          description="仅 direct 模式有效；开启后原图会进入长期上下文"
          disabled={deliveryMode !== "direct"}
        />
        <NumberField
          path="plugins.ai_group_chat.images.fetch_concurrency"
          label="取图并发数"
          placeholder="例如 16"
        />
        <NumberField
          path="plugins.ai_group_chat.images.download_timeout_seconds"
          label="下载超时（秒）"
          placeholder="例如 20"
        />
        <SwitchField
          path="plugins.ai_group_chat.images.forward_tool_enabled"
          label="启用合并转发图片工具"
        />
        <NumberField
          path="plugins.ai_group_chat.images.forward_max_per_call"
          label="单次转发上限"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.ai_group_chat.images.forward_max_per_turn"
          label="单轮转发上限"
          description="0 表示不限"
        />
      </SectionCard>

      <h3 className="col-span-full text-base font-medium">群列表</h3>
      <GroupsSection />
    </>
  );
}
