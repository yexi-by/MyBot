/** 插件页字段表与启用时写入的完整初始配置。 */

import { getIn, setIn } from "@/lib/path";
import type { JsonObject, PathSeg, PluginId } from "@/lib/types";

import {
  area,
  file,
  ids,
  int,
  model,
  num,
  provider,
  select,
  sw,
  tags,
  text,
} from "./helpers";
import type { FieldDef, SectionDef } from "./types";

export interface PluginMeta {
  id: PluginId;
  name: string;
  desc: string;
  /** 启用时写入的执行参数默认值（与 config.example.toml 一致）。 */
  execution: { consumers_count: number; priority: number };
}

export const PLUGINS: PluginMeta[] = [
  {
    id: "ai_group_chat",
    name: "AI 群聊",
    desc: "群友 @ 机器人时，用这里配置的模型、人格和工具回复。",
    execution: { consumers_count: 5, priority: 5 },
  },
  {
    id: "group_notice",
    name: "群通知",
    desc: "群成员进群、退群时发送提醒。",
    execution: { consumers_count: 5, priority: 10 },
  },
  {
    id: "auto_unban",
    name: "自动解禁",
    desc: "保护列表里的用户被禁言后自动解禁。",
    execution: { consumers_count: 1, priority: 10 },
  },
  {
    id: "recall_bot_image",
    name: "撤回图片",
    desc: "群友用命令撤回机器人发出的图片。",
    execution: { consumers_count: 5, priority: 90 },
  },
];

export function pluginMeta(id: PluginId): PluginMeta {
  const meta = PLUGINS.find((item) => item.id === id);
  if (!meta) throw new Error(`未知插件: ${id}`);
  return meta;
}

export function executionSection(id: PluginId): SectionDef {
  return {
    id: "execution",
    title: "执行参数",
    base: ["plugin_execution", "plugins", id],
    restart: true,
    desc: "启动期参数：消费者数量与事件路由优先级，重启进程后生效。",
    cols: 2,
    fields: [
      int("consumers_count", "并发消费者", { unit: "个", min: 1 }),
      int("priority", "事件优先级", { desc: "数值越大越先处理同一事件" }),
    ],
  };
}

/* ---------- AI 群聊 ---------- */

const AI: PathSeg[] = ["plugins", "ai_group_chat"];
const at = (...rest: PathSeg[]): PathSeg[] => [...AI, ...rest];

export const DEFAULT_ROLE_PROMPT = "ai_group_chat/prompts/roles/maodie.md";

function firstProvider(config: JsonObject): string {
  return Object.keys((getIn(config, ["llm", "providers"]) as JsonObject | undefined) ?? {})[0] ?? "";
}

export const GROUP_FIELDS: FieldDef[] = [
  text("id", "群号", { mono: true, placeholder: "QQ 群号", required: true }),
  int("max_context_tokens", "长期上下文预算", { unit: "Token", gt: 0, desc: "超出后在下一轮开始时压缩历史" }),
  file("system_prompt_file", "人格提示词", { required: true }),
  file("knowledge_base_file", "知识库", { nullable: true, placeholder: "留空不使用知识库" }),
];

export function defaultGroup(): JsonObject {
  return { id: "", system_prompt_file: DEFAULT_ROLE_PROMPT, max_context_tokens: 64000 };
}

const AI_MODEL: SectionDef = {
  id: "model",
  title: "主模型",
  base: at("model"),
  desc: "群聊对话使用的模型；图片以原始内容随请求发送，需选择支持图片输入的模型。",
  cols: 2,
  fields: [
    provider("provider"),
    model("name", "provider"),
  ],
};

const AI_RECENT: SectionDef = {
  id: "recent_chat",
  title: "最近群聊",
  base: at("recent_chat"),
  desc: "被 @ 时附带本次启动以来、这条消息之前的群聊；只随本轮请求发送，不写入长期上下文。",
  cols: 2,
  optional: {
    switchLabel: "开启最近群聊",
    offText: "未开启：模型只看得到 @ 它的那条消息和引用内容。",
    enable: (config) => setIn(config, at("recent_chat"), { max_messages: 30, max_chars_per_message: 200 }),
    disable: (config) => setIn(config, at("recent_chat"), null),
  },
  fields: [
    int("max_messages", "最多条数", { unit: "条", min: 1 }),
    int("max_chars_per_message", "单条字符上限", { unit: "字", min: 0, zero: "不截断" }),
  ],
};

const AI_IMAGES: SectionDef = {
  id: "images",
  title: "图片处理",
  base: at("images"),
  desc: "原图直接交给主模型；配置读取限制、历史保留与合并转发图片工具。",
  fields: [
    sw("retain_images", "图片跨轮保留", { wide: true, desc: "开启后原图进入长期上下文" }),
    select("oversize_behavior", "超限处理", [
      ["skip", "跳过并告知模型"],
      ["error", "立即报错"],
    ]),
    int("max_per_turn", "每轮最多图片", { unit: "张", min: 0, zero: "不限" }),
    int("max_image_bytes", "单图体积上限", { unit: "字节", min: 0, zero: "不限", bytes: true }),
    int("max_total_bytes_per_request", "单次请求总体积", { unit: "字节", min: 0, zero: "不限", bytes: true }),
    int("max_width", "最大宽度", { unit: "px", min: 0, zero: "不限" }),
    int("max_height", "最大高度", { unit: "px", min: 0, zero: "不限" }),
    select("image_detail", "多模态 detail", [
      ["omit", "omit · 不发送该字段"],
      ["auto", "auto"],
      ["low", "low"],
      ["high", "high"],
    ]),
    int("fetch_concurrency", "取图并发数", { min: 1 }),
    num("download_timeout_seconds", "下载超时", { unit: "秒", gt: 0, wide: true }),
    tags("allowed_mime_types", "允许的 MIME 类型", { span: 3, mono: true, unique: true, placeholder: "image/png", desc: "留空接受读取器能识别并交给 provider 的全部格式", addLabel: "添加 MIME 类型" }),
    sw("forward_tool_enabled", "合并转发图片工具", { desc: "允许模型把图片打包成合并转发发出" }),
    int("forward_max_per_call", "单次转发上限", { unit: "张", min: 0, zero: "不限" }),
    int("forward_max_per_turn", "单轮转发上限", { unit: "张", min: 0, zero: "不限" }),
  ],
};

const AI_VIDEOS: SectionDef = {
  id: "videos",
  title: "视频输入",
  base: at("videos"),
  desc: "仅适用于支持 video_url 的 Chat Completions 兼容服务；Responses API 收到视频时返回不支持错误，不会自动降级。",
  fields: [
    sw("enabled", "接受视频输入", { span: 2, desc: "视频只用于当前轮，长期上下文保留文字" }),
    int("max_per_turn", "每轮视频数量", { unit: "个", min: 0, zero: "不限", desc: "优先读取当前消息" }),
    int("max_video_bytes", "单个视频体积上限", { unit: "字节", min: 0, zero: "不限", bytes: true }),
    int("fetch_concurrency", "读取并发数", { min: 1 }),
    num("download_timeout_seconds", "下载超时", { unit: "秒", gt: 0 }),
  ],
};

const AI_BEHAVIOR: SectionDef = {
  id: "behavior",
  title: "对话行为",
  base: AI,
  table: "[plugins.ai_group_chat]",
  desc: "工具循环、通用要求、回复方式与上下文保留。人格、知识库与通用要求共同组成固定系统提示词。",
  fields: [
    int("max_tool_rounds", "最大工具轮数", { unit: "轮", min: 1 }),
    num("token_safety_factor", "Token 安全系数", { gt: 0, desc: "估算值乘以该系数后再与预算比较" }),
    int("forward_reply_threshold_chars", "转合并转发阈值", { unit: "字", min: 0, zero: "所有回复都合并转发" }),
    file("extra_requirements_file", "通用要求文件", { required: true, wide: true }),
    select("tool_result_retention", "工具结果长期保存", [
      ["off", "off · 不保存"],
      ["summary", "summary · 名称与状态"],
      ["full", "full · 参数与完整结果"],
    ]),
    sw("allow_mention_all", "允许 @全体", { desc: "关闭时模型写出 <At>all</At> 会收到可恢复错误" }),
    area("context_compression_notice", "上下文压缩提示语", { wide: true, desc: "压缩长期上下文前发到群里的提示" }),
  ],
};

const AI_HISTORY: SectionDef = {
  id: "history",
  title: "历史工具",
  base: at("history"),
  desc: "群历史分页与锚点前后的默认条数。",
  cols: 2,
  fields: [
    int("default_limit", "默认单页条数", { unit: "条", min: 1 }),
    int("max_per_call", "单页最大条数", { unit: "条", min: 0, zero: "不限" }),
    int("default_before_count", "锚点前默认条数", { unit: "条", min: 0 }),
    int("default_after_count", "锚点后默认条数", { unit: "条", min: 0 }),
  ],
};

const AI_FILES: SectionDef = {
  id: "files",
  title: "群文件工具",
  base: at("files"),
  desc: "群文件列表工具的返回数量。",
  cols: 2,
  fields: [
    int("default_count", "默认文件数量", { unit: "个", min: 1 }),
    int("max_per_call", "单次最大文件数量", { unit: "个", min: 0, zero: "不限" }),
  ],
};

const AI_FORMATTING: SectionDef = {
  id: "formatting",
  title: "消息格式化",
  base: at("formatting"),
  desc: "把消息转成模型可读文本时的截断与展开深度；字符和条目为 0、深度为 -1 时不限。",
  fields: [
    int("field_text_limit", "普通字段字符上限", { unit: "字", min: 0, zero: "不限" }),
    int("json_text_limit", "JSON 字符上限", { unit: "字", min: 0, zero: "不限" }),
    int("markdown_text_limit", "Markdown 字符上限", { unit: "字", min: 0, zero: "不限" }),
    int("forward_max_items", "内嵌转发条目上限", { unit: "条", min: 0, zero: "不限" }),
    int("forward_max_depth", "转发展开深度", { unit: "层", min: -1, neg: "不限" }),
    int("nested_text_search_max_depth", "卡片文本搜索深度", { unit: "层", min: -1, neg: "不限" }),
  ],
};

const AI_TOKENS: SectionDef = {
  id: "token_estimator",
  title: "Token 估算",
  base: at("token_estimator"),
  desc: "没有 tokenizer 时估算上下文长度的参数，按当前模型微调。",
  fields: [
    int("request_overhead_tokens", "每次请求固定开销", { unit: "Token", min: 0 }),
    int("message_overhead_tokens", "每条消息固定开销", { unit: "Token", min: 0 }),
    int("tool_call_overhead_tokens", "每次工具调用开销", { unit: "Token", min: 0 }),
    int("image_tokens", "每张图片", { unit: "Token", min: 0 }),
    num("ascii_tokens_per_character", "ASCII 每字符", { unit: "Token", min: 0 }),
    num("non_ascii_tokens_per_character", "非 ASCII 每字符", { unit: "Token", min: 0 }),
  ],
};

/** AI 群聊页按行排布：主模型之后紧跟群列表（单独渲染），其余分节依次往下。 */
export const AI_ROWS_TOP: SectionDef[][] = [[AI_MODEL]];

export const AI_ROWS_REST: SectionDef[][] = [
  [AI_RECENT],
  [AI_IMAGES],
  [AI_VIDEOS],
  [AI_BEHAVIOR],
  [AI_HISTORY, AI_FILES],
  [AI_FORMATTING],
  [AI_TOKENS],
];

export const AI_GROUPS_BASE = at("groups");

export const AI_SECTIONS: SectionDef[] = [...AI_ROWS_TOP, ...AI_ROWS_REST].flat();

function defaultAIGroupChat(config: JsonObject): JsonObject {
  const provider = firstProvider(config);
  return {
    model: { provider, name: "" },
    images: {
      max_per_turn: 0,
      fetch_concurrency: 16,
      download_timeout_seconds: 20,
      max_image_bytes: 0,
      max_total_bytes_per_request: 0,
      max_width: 0,
      max_height: 0,
      allowed_mime_types: [],
      oversize_behavior: "skip",
      image_detail: "auto",
      retain_images: false,
      forward_tool_enabled: true,
      forward_max_per_call: 0,
      forward_max_per_turn: 0,
    },
    videos: {
      enabled: false,
      max_per_turn: 2,
      fetch_concurrency: 2,
      download_timeout_seconds: 60,
      max_video_bytes: 20971520,
    },
    formatting: {
      field_text_limit: 0,
      json_text_limit: 0,
      markdown_text_limit: 0,
      forward_max_items: 0,
      forward_max_depth: -1,
      nested_text_search_max_depth: -1,
    },
    history: { default_limit: 20, max_per_call: 0, default_before_count: 10, default_after_count: 10 },
    recent_chat: { max_messages: 30, max_chars_per_message: 200 },
    files: { default_count: 50, max_per_call: 0 },
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
    extra_requirements_file: "ai_group_chat/prompts/extra_requirements.md",
    allow_mention_all: false,
    tool_result_retention: "off",
    groups: [],
  };
}

/* ---------- 其他插件 ---------- */

const GROUP_NOTICE: SectionDef = {
  id: "group_notice",
  title: "群通知",
  base: ["plugins", "group_notice"],
  desc: "在哪些群发送成员变动提醒。",
  fields: [
    ids("groups", "生效群号", { span: 2, placeholder: "QQ 群号", addLabel: "添加群号" }),
    sw("send_avatar", "发送成员头像", { desc: "提醒消息附带成员头像" }),
  ],
};

const AUTO_UNBAN: SectionDef = {
  id: "auto_unban",
  title: "保护名单",
  base: ["plugins", "auto_unban"],
  desc: "名单里的用户被禁言后会自动解禁。",
  fields: [ids("protected_users", "保护用户 QQ", { span: 3, placeholder: "QQ 号", addLabel: "添加用户" })],
};

const RECALL: SectionDef = {
  id: "recall_bot_image",
  title: "撤回命令",
  base: ["plugins", "recall_bot_image"],
  desc: "群友回复机器人图片并发送命令即可撤回。",
  cols: 2,
  fields: [
    text("command", "撤回命令", { mono: true, required: true }),
    int("failure_detail_max_chars", "失败详情最大字符数", { unit: "字", min: 0, zero: "不截断" }),
  ],
};

/** 除 AI 群聊外各插件页的分节行。 */
export const PLUGIN_ROWS: Record<Exclude<PluginId, "ai_group_chat">, SectionDef[][]> = {
  group_notice: [[GROUP_NOTICE]],
  auto_unban: [[AUTO_UNBAN]],
  recall_bot_image: [[RECALL]],
};

export function defaultPluginConfig(id: PluginId, config: JsonObject): JsonObject {
  switch (id) {
    case "ai_group_chat":
      return defaultAIGroupChat(config);
    case "group_notice":
      return { groups: [], send_avatar: true };
    case "auto_unban":
      return { protected_users: [] };
    case "recall_bot_image":
      return { command: "#撤回", failure_detail_max_chars: 160 };
  }
}

/** 被 AI 群聊引用、不能保存为空的提示词文件。 */
export function requiredPromptFiles(config: JsonObject): Set<string> {
  const ai = getIn(config, AI) as JsonObject | undefined;
  const files = new Set<string>();
  if (!ai) return files;
  const add = (value: unknown) => {
    if (typeof value === "string" && value.trim() !== "") files.add(value.trim());
  };
  add(ai.extra_requirements_file);
  for (const group of (Array.isArray(ai.groups) ? ai.groups : []) as JsonObject[]) {
    add(group?.system_prompt_file);
  }
  return files;
}

export interface FileReference {
  label: string;
  required: boolean;
}

/** config/ 内文件被当前配置引用的情况，用于文件树标记。 */
export function fileReferences(config: JsonObject): Map<string, FileReference[]> {
  const refs = new Map<string, FileReference[]>();
  const push = (value: unknown, label: string, required: boolean) => {
    if (typeof value !== "string" || value.trim() === "") return;
    const key = value.trim();
    refs.set(key, [...(refs.get(key) ?? []), { label, required }]);
  };
  const ai = getIn(config, AI) as JsonObject | undefined;
  if (ai) {
    push(ai.extra_requirements_file, "通用要求", true);
    const groups = (Array.isArray(ai.groups) ? ai.groups : []) as JsonObject[];
    groups.forEach((group, index) => {
      const name = typeof group?.id === "string" && group.id ? `群 ${group.id}` : `第 ${index + 1} 个群`;
      push(group?.system_prompt_file, `${name} 人格`, true);
      push(group?.knowledge_base_file, `${name} 知识库`, false);
    });
  }
  return refs;
}
