/** 插件配置页调度：启用开关 + 按插件分发字段表单。 */

import { useState } from "react";
import { useFormContext } from "react-hook-form";

import { SectionCard } from "@/components/SectionCard";
import { SettingsGrid } from "@/components/SettingsGrid";
import { ModelRefField } from "@/components/ModelRefField";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { defaultPluginConfig, pluginMeta } from "@/lib/configMeta";
import {
  NumberField,
  StringListField,
  SwitchField,
  TextField,
} from "@/lib/fields";
import type { MyBotConfigData, PluginId } from "@/lib/types";

import AIGroupChatFields from "./AIGroupChatFields";

function GroupNoticeFields() {
  return (
    <SectionCard title="群通知配置">
      <div className="xl:col-span-2">
        <StringListField
          path="plugins.group_notice.groups"
          label="生效群号"
          placeholder="QQ 群号"
          addLabel="添加群号"
        />
      </div>
      <SwitchField
        path="plugins.group_notice.send_avatar"
        label="发送成员头像"
        description="成员变动提醒消息中附带头像"
      />
    </SectionCard>
  );
}

function AutoUnbanFields() {
  return (
    <SectionCard title="自动解禁配置">
      <div className="xl:col-span-2">
        <StringListField
          path="plugins.auto_unban.protected_users"
          label="保护用户列表"
          description="列表中的用户被禁言后自动解禁"
          placeholder="QQ 号"
          addLabel="添加用户"
        />
      </div>
    </SectionCard>
  );
}

function ImageGenerateFields() {
  return (
    <>
      <SectionCard title="生图模型">
        <ModelRefField path="plugins.image_generate.model" />
      </SectionCard>
      <SectionCard title="生图配置">
        <div className="xl:col-span-2">
          <StringListField
            path="plugins.image_generate.groups"
            label="生效群号"
            placeholder="QQ 群号"
            addLabel="添加群号"
          />
        </div>
        <NumberField
          path="plugins.image_generate.fetch_concurrency"
          label="取图并发数"
          placeholder="例如 16"
        />
        <NumberField
          path="plugins.image_generate.download_timeout_seconds"
          label="下载超时（秒）"
          placeholder="例如 20"
        />
        <NumberField
          path="plugins.image_generate.max_input_image_bytes"
          label="输入单图最大字节"
          description="0 表示不限"
        />
        <TextField path="plugins.image_generate.command" label="生图命令" />
        <TextField path="plugins.image_generate.help_command" label="帮助命令" />
      </SectionCard>
    </>
  );
}

function NeavoImageGenerateFields() {
  return (
    <>
      <SectionCard title="Neavo 服务">
        <TextField
          path="plugins.neavo_image_generate.base_url"
          label="服务地址"
          placeholder="https://image-api.example.com"
        />
        <TextField
          path="plugins.neavo_image_generate.api_token"
          label="API Token"
          description="留空时不发送 Authorization"
        />
      </SectionCard>
      <SectionCard title="生图配置">
        <div className="xl:col-span-2">
          <StringListField
            path="plugins.neavo_image_generate.groups"
            label="生效群号"
            placeholder="QQ 群号"
            addLabel="添加群号"
          />
        </div>
        <NumberField
          path="plugins.neavo_image_generate.poll_interval_seconds"
          label="轮询间隔（秒）"
          placeholder="例如 3"
        />
        <NumberField
          path="plugins.neavo_image_generate.generation_timeout_seconds"
          label="生成超时（秒）"
          placeholder="例如 600"
        />
        <NumberField
          path="plugins.neavo_image_generate.request_timeout_seconds"
          label="请求超时（秒）"
          placeholder="例如 30"
        />
        <NumberField
          path="plugins.neavo_image_generate.max_prompt_chars"
          label="生图描述最大字符数"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.neavo_image_generate.max_input_image_bytes"
          label="反推输入最大字节"
          description="0 表示不限"
        />
        <NumberField
          path="plugins.neavo_image_generate.max_output_image_bytes"
          label="生成结果最大字节"
          description="0 表示不限"
        />
        <div className="xl:col-span-2">
          <StringListField
            path="plugins.neavo_image_generate.allowed_input_mime_types"
            label="允许的反推输入 MIME 类型"
            description="空列表表示不限制；图片内容仍需可识别"
            placeholder="如 image/png"
            addLabel="添加 MIME 类型"
          />
        </div>
        <NumberField
          path="plugins.neavo_image_generate.max_consecutive_poll_errors"
          label="连续轮询错误上限"
          description="0 表示只受生成总超时限制"
          placeholder="例如 3"
        />
        <TextField path="plugins.neavo_image_generate.generate_command" label="生图命令" />
        <TextField path="plugins.neavo_image_generate.describe_command" label="反推命令" />
      </SectionCard>
    </>
  );
}

function RecallBotImageFields() {
  return (
    <SectionCard title="撤回图片配置">
      <p className="text-sm text-muted-foreground xl:col-span-2">
        修改机器人图片撤回命令。
      </p>
      <TextField path="plugins.recall_bot_image.command" label="撤回命令" />
      <NumberField
        path="plugins.recall_bot_image.failure_detail_max_chars"
        label="失败详情最大字符数"
        description="0 表示不截断"
      />
    </SectionCard>
  );
}

function PluginFields({ pluginId }: { pluginId: PluginId }) {
  switch (pluginId) {
    case "ai_group_chat":
      return <AIGroupChatFields />;
    case "group_notice":
      return <GroupNoticeFields />;
    case "auto_unban":
      return <AutoUnbanFields />;
    case "image_generate":
      return <ImageGenerateFields />;
    case "neavo_image_generate":
      return <NeavoImageGenerateFields />;
    case "recall_bot_image":
      return <RecallBotImageFields />;
  }
}

export default function PluginPage({ pluginId }: { pluginId: PluginId }) {
  const meta = pluginMeta(pluginId);
  const { watch, setValue, getValues } = useFormContext<MyBotConfigData>();
  const [confirmDisable, setConfirmDisable] = useState(false);
  const path = `plugins.${pluginId}` as const;
  const enabled = watch(path) != null;

  const enablePlugin = () => {
    setValue(path, defaultPluginConfig(pluginId, getValues()), {
      shouldDirty: true,
    });
  };
  const disablePlugin = () => {
    setValue(path, null, { shouldDirty: true });
    setConfirmDisable(false);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div>
          <h2 className="text-lg font-semibold">{meta.name}</h2>
          <p className="text-sm text-muted-foreground">{meta.description}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">
            {enabled ? "已启用" : "已禁用"}
          </span>
          <Switch
            aria-label={`${enabled ? "禁用" : "启用"}${meta.name}`}
            checked={enabled}
            onCheckedChange={(checked) => {
              if (checked) {
                enablePlugin();
              } else {
                setConfirmDisable(true);
              }
            }}
          />
        </div>
      </div>

      <SettingsGrid>
        {enabled ? (
          <>
          <PluginFields pluginId={pluginId} />
          </>
        ) : (
          <SectionCard title="插件配置">
            <p className="text-sm text-muted-foreground xl:col-span-2">
              插件已禁用；打开开关后将写入该插件的完整初始配置节。
            </p>
          </SectionCard>
        )}
        <SectionCard title="执行参数" description="修改后需要重启进程。">
          <NumberField
            path={`plugin_execution.plugins.${pluginId}.consumers_count`}
            label="并发消费者数量"
          />
          <NumberField
            path={`plugin_execution.plugins.${pluginId}.priority`}
            label="事件处理优先级"
          />
        </SectionCard>
      </SettingsGrid>

      <Dialog open={confirmDisable} onOpenChange={setConfirmDisable}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>禁用 {meta.name}？</DialogTitle>
            <DialogDescription>
              禁用会从配置文件中删除该插件的整个配置节，自动保存后立即热生效。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDisable(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={disablePlugin}>
              禁用插件
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
