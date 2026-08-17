/** 插件元信息与启用时的默认配置（默认值与 app/config/schemas.py 对齐）。 */

import type {
  AIGroupChatConfig,
  MyBotConfigData,
  PluginId,
  PluginsConfig,
} from "./types";

export interface PluginMeta {
  id: PluginId;
  name: string;
  description: string;
}

export const PLUGIN_METAS: PluginMeta[] = [
  {
    id: "ai_group_chat",
    name: "AI 群聊",
    description: "群消息 AI 对话，含模型、视觉、图片与群列表配置",
  },
  {
    id: "group_notice",
    name: "群通知",
    description: "群成员变动提醒",
  },
  {
    id: "auto_unban",
    name: "自动解禁",
    description: "保护列表中的用户被禁言后自动解禁",
  },
  {
    id: "image_generate",
    name: "生图",
    description: "OpenAI Images 生图插件",
  },
  {
    id: "neavo_image_generate",
    name: "Neavo 生图",
    description: "Neavo 图像生成服务插件",
  },
  {
    id: "recall_bot_image",
    name: "撤回图片",
    description: "撤回机器人发送的图片，无额外配置项",
  },
];

export function pluginMeta(id: PluginId): PluginMeta {
  const meta = PLUGIN_METAS.find((item) => item.id === id);
  if (!meta) {
    throw new Error(`未知插件: ${id}`);
  }
  return meta;
}

/** 生成 AI 群聊默认配置；第一个 provider 作为模型默认引用。 */
function defaultAIGroupChat(config: MyBotConfigData): AIGroupChatConfig {
  const providerIds = Object.keys(config.llm?.providers ?? {});
  const provider = providerIds[0] ?? "";
  return {
    model: { provider, name: "", supports_images: false },
    vision: {
      model: { provider, name: "" },
      system_prompt_file: "ai_group_chat/prompts/vision/system.md",
      user_prompt_file: "ai_group_chat/prompts/vision/user.md",
    },
    images: {},
    groups: [],
  };
}

/** 返回指定插件启用时的默认配置节。 */
export function defaultPluginConfig(
  id: PluginId,
  config: MyBotConfigData,
): NonNullable<PluginsConfig[PluginId]> {
  switch (id) {
    case "ai_group_chat":
      return defaultAIGroupChat(config);
    case "group_notice":
      return { groups: [], send_avatar: true };
    case "auto_unban":
      return { protected_users: [] };
    case "image_generate": {
      const provider = Object.keys(config.llm?.providers ?? {})[0] ?? "";
      return { groups: [], model: { provider, name: "" } };
    }
    case "neavo_image_generate":
      return {
        groups: [],
        base_url: "",
        api_token: null,
        poll_interval_seconds: 3,
        generation_timeout_seconds: 600,
        request_timeout_seconds: 30,
        max_image_bytes: 20971520,
      };
    case "recall_bot_image":
      return {};
  }
}

/** 各配置节的中文展示名，用于重启提示。 */
export const SECTION_LABELS: Record<string, string> = {
  app: "应用",
  server: "服务监听",
  napcat: "NapCat 连接",
  storage: "存储",
  network: "网络",
  logging: "日志",
  llm: "LLM Providers",
  mcp: "MCP 服务",
  database: "数据库",
};
