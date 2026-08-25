/** 插件元信息与启用时写入的完整初始配置。 */

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
    description: "撤回机器人发送的图片，可配置命令和失败详情长度",
  },
];

export function pluginMeta(id: PluginId): PluginMeta {
  const meta = PLUGIN_METAS.find((item) => item.id === id);
  if (!meta) {
    throw new Error(`未知插件: ${id}`);
  }
  return meta;
}

/** 生成 AI 群聊完整初始配置；第一个 provider 作为模型初始引用。 */
function defaultAIGroupChat(config: MyBotConfigData): AIGroupChatConfig {
  const providerIds = Object.keys(config.llm?.providers ?? {});
  const provider = providerIds[0] ?? "";
  return {
    model: { provider, name: "", supports_images: false },
    vision: {
      model: { provider, name: "" },
      system_prompt_file: "ai_group_chat/prompts/vision/system.md",
      user_prompt_file: "ai_group_chat/prompts/vision/user.md",
      max_attempts: 5,
      retry_delay_seconds: 0.25,
      retry_max_delay_seconds: 0,
      retain_descriptions: true,
    },
    images: {
      max_per_turn: 0,
      fetch_concurrency: 16,
      download_timeout_seconds: 20,
      max_image_bytes: 0,
      max_total_bytes_per_request: 0,
      max_width: 0,
      max_height: 0,
      allowed_mime_types: [],
      delivery_mode: "vision",
      oversize_behavior: "skip",
      image_detail: "auto",
      retain_images: false,
      forward_tool_enabled: true,
      forward_max_per_call: 0,
      forward_max_per_turn: 0,
    },
    formatting: {
      field_text_limit: 0,
      json_text_limit: 0,
      markdown_text_limit: 0,
      forward_max_items: 0,
      forward_max_depth: -1,
      nested_text_search_max_depth: -1,
    },
    history: {
      default_limit: 20,
      max_per_call: 0,
      default_before_count: 10,
      default_after_count: 10,
    },
    files: {
      default_count: 50,
      max_per_call: 0,
    },
    token_estimator: {
      request_overhead_tokens: 128,
      message_overhead_tokens: 16,
      tool_call_overhead_tokens: 64,
      image_tokens: 1024,
      ascii_tokens_per_character: 1,
      non_ascii_tokens_per_character: 2,
    },
    max_tool_rounds: 16,
    token_safety_factor: 1.05,
    context_compression_notice: "上下文有点长，我先整理一下记忆，稍等我几秒喵~",
    forward_reply_threshold_chars: 1000,
    show_reasoning: false,
    retain_reasoning: false,
    debug_dump_messages: true,
    debug_dump_directory: "logs/ai_group_chat_debug",
    extra_requirements_file: "ai_group_chat/prompts/extra_requirements.md",
    allow_mention_all: false,
    tool_result_retention: "off",
    groups: [],
  };
}

/** 返回指定插件启用时写入的完整配置节。 */
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
      return {
        groups: [],
        model: { provider, name: "" },
        fetch_concurrency: 16,
        download_timeout_seconds: 20,
        max_input_image_bytes: 0,
        command: "/生图",
        help_command: "/help生图",
      };
    }
    case "neavo_image_generate":
      return {
        groups: [],
        base_url: "",
        api_token: null,
        poll_interval_seconds: 3,
        generation_timeout_seconds: 600,
        request_timeout_seconds: 30,
        max_prompt_chars: 4096,
        max_input_image_bytes: 10485760,
        max_output_image_bytes: 20971520,
        allowed_input_mime_types: ["image/jpeg", "image/png", "image/webp"],
        max_consecutive_poll_errors: 3,
        generate_command: "#生图",
        describe_command: "#反推",
      };
    case "recall_bot_image":
      return { command: "#撤回", failure_detail_max_chars: 160 };
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
  plugin_execution: "插件执行",
};
