import { getAt, object } from "../lib/types";
import type { Json, ObjectValue, Path } from "../lib/types";
export type FieldType =
  | "text"
  | "secret"
  | "number"
  | "toggle"
  | "select"
  | "list"
  | "map"
  | "model";
export interface Option {
  value: string;
  label: string;
}
export interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  description: string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: Option[];
  span?: 2 | 3;
  nullable?: boolean;
  placeholder?: string;
  providerId?: string;
  disabled?: (config: ObjectValue, base: Path) => string | false;
  choices?: (config: ObjectValue) => Option[];
}
export interface SectionDef {
  id: string;
  title: string;
  description: string;
  fields: FieldDef[];
  cols?: 2;
}
const text = (
  key: string,
  label: string,
  description: string,
  extra: Partial<FieldDef> = {},
): FieldDef => ({ key, label, description, type: "text", ...extra });
const number = (
  key: string,
  label: string,
  description: string,
  min: number,
  extra: Partial<FieldDef> = {},
): FieldDef => ({ key, label, description, type: "number", min, ...extra });
export const serverSection: SectionDef = {
  id: "server",
  title: "控制台服务",
  description: "服务监听地址；修改后需要重启。",
  cols: 2,
  fields: [
    text("host", "监听地址", "面向可信内网使用。"),
    number("port", "监听端口", "WebUI 与 API 共用端口。", 1, { max: 65535 }),
  ],
};
export const napcatSection: SectionDef = {
  id: "napcat",
  title: "NapCat 连接",
  description: "反向 WebSocket 接入；QQ 数据按需读取。",
  fields: [
    text("token", "连接密钥", "与 NapCat 反向 WebSocket 配置的 Token 一致。", {
      type: "secret",
    }),
    text(
      "expected_bot_id",
      "机器人 QQ",
      "可限制接入的机器人账号；这不是群成员权限。",
    ),
    number(
      "action_timeout",
      "接口超时",
      "超时的副作用操作不会自动重试。",
      0.1,
      { unit: "秒" },
    ),
    number("media_timeout", "媒体读取超时", "单项媒体超时会独立报告。", 0.1, {
      unit: "秒",
    }),
    number(
      "media_max_bytes",
      "单项媒体上限",
      "只限制读取大小，不建立媒体归档。",
      1,
      { unit: "字节" },
    ),
    number(
      "media_concurrency",
      "媒体读取并发",
      "同批媒体的最大并发读取数。",
      1,
      { max: 32 },
    ),
  ],
};
export const runtimeSection: SectionDef = {
  id: "runtime",
  title: "运行边界",
  description: "这些是 Harness 运行限制，不是模型生成参数。",
  fields: [
    number("concurrent_conversations", "并发会话", "同一会话始终串行。", 1, {
      max: 32,
    }),
    number(
      "max_model_rounds",
      "模型请求上限",
      "本轮普通模型调用、上下文压缩和超限恢复的实际 HTTP 请求共同计数。",
      1,
      { max: 100 },
    ),
    number(
      "turn_timeout",
      "单轮时限",
      "超时后结束本轮，已提交的 QQ 操作不会撤销。",
      0.1,
      { unit: "秒" },
    ),
    number(
      "history_count",
      "历史消息数",
      "每轮最多从 NapCat 读取的近期消息数。",
      0,
      { max: 300 },
    ),
    number(
      "max_pending_conversations",
      "等待会话上限",
      "避免持续积压；队列只在内存中。",
      1,
      { max: 1000 },
    ),
    number(
      "max_pending_messages",
      "待处理消息上限",
      "运行期间的新唤醒合并到下一轮。",
      1,
      { max: 100 },
    ),
    number(
      "tool_result_char_limit",
      "工具结果字符上限",
      "大结果按边界截取并明确提示。",
      1000,
      { unit: "字符" },
    ),
    number("activity_limit", "活动展示上限", "仅保留内存中的近期活动。", 10, {
      max: 2000,
    }),
  ],
};
export const providerSection: SectionDef = {
  id: "provider",
  title: "模型连接",
  description: "只使用两种 OpenAI 协议格式，不添加供应商专用分支。",
  fields: [
    text("protocol", "协议", "Responses 或 Chat Completions。", {
      type: "select",
      options: [
        { value: "responses", label: "Responses" },
        { value: "chat_completions", label: "Chat Completions" },
      ],
    }),
    text("base_url", "API 地址", "填写 API 根地址，通常以 /v1 结尾。"),
    text("api_key", "API Key", "由后端用于模型请求，不进入 Agent 上下文。", {
      type: "secret",
    }),
    text("model", "模型名称", "候选列表不可用时仍可手动输入。", {
      type: "model",
    }),
    number(
      "max_context_tokens",
      "最大上下文容量",
      "填写模型实际总上下文窗口。占用达到 80% 强制压缩，目标约 50%，保留最近完整工具轮次；不是回复长度限制。",
      4096,
      { step: 1, unit: "token" },
    ),
    text(
      "reasoning_effort",
      "思考等级",
      "仅在接口明确支持时选择；默认不发送此参数，不支持时直接报错，不会静默降级。",
      {
        type: "select",
        nullable: true,
        placeholder: "跟随接口默认（不发送）",
        options: [
          "none",
          "minimal",
          "low",
          "medium",
          "high",
          "xhigh",
          "max",
        ].map((value) => ({ value, label: value })),
      },
    ),
    number("timeout", "请求超时", "模型接口超时。", 0.1, { unit: "秒" }),
    text("proxy", "HTTP 代理", "留空表示不使用代理。", { nullable: true }),
    text(
      "modalities",
      "输入模态",
      "声明模型实际支持的模态；不支持的媒体会明确报错。",
      {
        type: "list",
        span: 2,
        options: ["text", "image", "audio", "video"].map((value) => ({
          value,
          label: value,
        })),
      },
    ),
    text(
      "store_supported",
      "发送 store: false",
      "接口支持时显式关闭服务端会话存储。",
      { type: "toggle" },
    ),
  ],
};
export const activeProviderSection: SectionDef = {
  id: "active-provider",
  title: "当前模型",
  description: "所有启用的会话使用这一模型连接。",
  fields: [
    text("active_provider", "生效 Provider", "选择已配置的模型连接。", {
      type: "select",
      span: 3,
      choices: (config) =>
        Object.keys(object(config.providers)).map((value) => ({
          value,
          label: value,
        })),
    }),
  ],
};
export const conversationSection: SectionDef = {
  id: "conversation",
  title: "会话行为",
  description: "唤醒只提供对话输入，是否行动由 Agent 判断。",
  cols: 2,
  fields: [
    text("enabled", "启用会话", "停用会结束后续新唤醒。", { type: "toggle" }),
    text(
      "keywords",
      "唤醒关键词",
      "群内艾特、引用机器人消息也会唤醒；私聊启用后逐条触发。",
      { type: "list" },
    ),
  ],
};
export const policySection: SectionDef = {
  id: "policy",
  title: "动作边界",
  description: "权限属于当前群中的 Agent，不按发言者身份区分。",
  fields: [
    text("allow_at_all", "允许 @全体", "仍受 QQ 平台实际能力限制。", {
      type: "toggle",
    }),
    number(
      "max_sends_per_turn",
      "每轮发送上限",
      "只统计显式发送；普通模型正文不会自动发送。",
      1,
      { max: 100 },
    ),
    text(
      "cross_private",
      "允许跨会话私聊",
      "显式开放当前来源会话的跨私聊能力。",
      { type: "toggle" },
    ),
    text(
      "cross_groups",
      "允许访问的其他群",
      "默认只作用于当前会话；填写明确的目标群号。",
      { type: "list", span: 3 },
    ),
  ],
};
export const mcpSection: SectionDef = {
  id: "mcp-server",
  title: "MCP 服务",
  description: "工具仍需在群策略中开放，连接成功不会自动授权。",
  fields: [
    text("enabled", "启用连接", "停用后不再提供该服务工具。", {
      type: "toggle",
    }),
    text("transport", "传输", "只支持 stdio 与 Streamable HTTP。", {
      type: "select",
      options: [
        { value: "streamable_http", label: "Streamable HTTP" },
        { value: "stdio", label: "stdio" },
      ],
    }),
    number("timeout", "调用超时", "超时返回明确状态，不重放副作用。", 0.1, {
      unit: "秒",
    }),
    text("url", "服务 URL", "Streamable HTTP 接口地址。", {
      span: 3,
      disabled: (config, base) =>
        getAt(config, [...base, "transport"]) !== "streamable_http" &&
        "仅 HTTP 传输使用",
    }),
    text("command", "启动命令", "由配置指定，模型不能改变服务启动命令。", {
      disabled: (config, base) =>
        getAt(config, [...base, "transport"]) !== "stdio" &&
        "仅 stdio 传输使用",
    }),
    text("args", "命令参数", "每项作为一个独立参数。", {
      type: "list",
      span: 2,
      disabled: (config, base) =>
        getAt(config, [...base, "transport"]) !== "stdio" &&
        "仅 stdio 传输使用",
    }),
    text("env", "环境变量", "仅提供给该 MCP 服务。", { type: "map", span: 3 }),
    text("headers", "HTTP 请求头", "认证等连接信息不进入模型上下文。", {
      type: "map",
      span: 3,
    }),
    text(
      "read_only_tools",
      "只读工具标记",
      "填写远端原始工具名。标记为只读后，失败可由 Agent 再次尝试；调用权限仍由群或私聊策略控制。",
      { type: "list", span: 3 },
    ),
  ],
};
export const personaSection: SectionDef = {
  id: "persona",
  title: "人格与知识",
  description: "安全与工具规则独立加载；人格只影响表达，不能改变权限。",
  fields: [
    text(
      "persona_file",
      "默认人格文件",
      "配置目录内的相对路径，不能保存为空。",
      { span: 3 },
    ),
    text(
      "knowledge_files",
      "知识库文件",
      "按顺序加载；新内容在下一轮对话生效。",
      { type: "list", span: 3 },
    ),
  ],
};
export const sandboxSection: SectionDef = {
  id: "sandbox",
  title: "共享沙箱配置",
  description: "获准使用沙箱的会话共享文件；文件持久化，执行状态不恢复。",
  fields: [
    text("enabled", "启用沙箱", "使用代码工具还需要当前群开放相应能力。", {
      type: "toggle",
    }),
    text("runner_url", "Runner 地址", "可信控制进程地址，不暴露给执行代码。"),
    text("runner_token", "Runner 密钥", "不注入执行容器。", { type: "secret" }),
    number(
      "quota_bytes",
      "共享磁盘总限额",
      "默认 50 GB；必须由底层硬配额落实。",
      1_000_000,
      { unit: "字节" },
    ),
    number("cpus", "CPU 上限", "所有代码执行共享此限制。", 0.1, { step: 0.1 }),
    number(
      "memory_bytes",
      "内存上限",
      "默认 4 GiB，不把磁盘空间伪装成内存盘。",
      134_217_728,
      { unit: "字节" },
    ),
    number("pids_limit", "进程上限", "限制派生进程，结束时清理执行进程。", 16),
    number(
      "command_timeout",
      "命令时限",
      "超时取消代码执行，文件继续保留。",
      0.1,
      { max: 600, unit: "秒" },
    ),
    number("output_limit", "输出大小上限", "终端结果只在内存中。", 1024, {
      max: 16_777_216,
      unit: "字节",
    }),
  ],
};
export const parameterSection: SectionDef = {
  id: "parameter-rule",
  title: "参数约束",
  description: "只限制工具参数，不判断发言者身份。",
  fields: [
    number("minimum", "最小值", "留空不设置数值下限。", -Number.MAX_VALUE, {
      nullable: true,
    }),
    number("maximum", "最大值", "留空不设置数值上限。", -Number.MAX_VALUE, {
      nullable: true,
    }),
    number("max_length", "最大长度", "限制文本或列表长度；留空不限制。", 0, {
      nullable: true,
    }),
    text("allowed", "允许的值", "每项可为文本、数字或布尔值。", {
      type: "list",
      span: 3,
    }),
  ],
};
export const allSections = [
  serverSection,
  napcatSection,
  runtimeSection,
  providerSection,
  activeProviderSection,
  conversationSection,
  policySection,
  mcpSection,
  personaSection,
  sandboxSection,
];
export function defaultPolicy(): ObjectValue {
  return {
    categories: {
      "messages.read": true,
      "messages.send": true,
      "members.read": true,
    },
    tools: {},
    parameters: {},
    allow_at_all: false,
    cross_groups: [],
    cross_private: false,
    max_sends_per_turn: 5,
  };
}
export function defaultConversation(): ObjectValue {
  return { enabled: false, keywords: [], policy: defaultPolicy() };
}
export function defaultProvider(): ObjectValue {
  return {
    protocol: "responses",
    base_url: "https://api.openai.com/v1",
    api_key: "",
    model: "",
    max_context_tokens: 128000,
    reasoning_effort: null,
    timeout: 120,
    proxy: null,
    modalities: ["text", "image"],
    store_supported: true,
  };
}
export function defaultMcp(): ObjectValue {
  return {
    enabled: false,
    transport: "streamable_http",
    url: "",
    command: "",
    args: [],
    env: {},
    headers: {},
    timeout: 60,
    read_only_tools: [],
  };
}
export function fieldInstances(
  config: ObjectValue,
): { field: FieldDef; path: Path; section: string }[] {
  const output: { field: FieldDef; path: Path; section: string }[] = [];
  const add = (section: SectionDef, base: Path) =>
    section.fields.forEach((field) =>
      output.push({
        field,
        path: [...base, field.key],
        section: section.title,
      }),
    );
  add(serverSection, ["server"]);
  add(napcatSection, ["napcat"]);
  add(runtimeSection, ["runtime"]);
  add(activeProviderSection, []);
  add(personaSection, []);
  add(sandboxSection, ["sandbox"]);
  for (const id of Object.keys(object(config.providers)))
    add(providerSection, ["providers", id]);
  for (const id of Object.keys(object(config.mcp)))
    add(mcpSection, ["mcp", id]);
  for (const id of Object.keys(object(config.groups))) {
    add(conversationSection, ["groups", id]);
    add(policySection, ["groups", id, "policy"]);
  }
  add(conversationSection, ["private"]);
  add(policySection, ["private", "policy"]);
  return output;
}
export function validateClient(
  config: ObjectValue,
): { path: Path; message: string }[] {
  const errors: { path: Path; message: string }[] = [];
  for (const { field, path } of fieldInstances(config)) {
    const value = getAt(config, path);
    if (value === null && field.nullable) continue;
    if (
      field.type === "number" &&
      (typeof value !== "number" ||
        !Number.isFinite(value) ||
        (field.min !== undefined && value < field.min) ||
        (field.max !== undefined && value > field.max))
    )
      errors.push({ path, message: field.label + "超出有效范围" });
    else if (
      field.type === "number" &&
      field.step === 1 &&
      !Number.isInteger(value)
    )
      errors.push({ path, message: field.label + "必须为整数" });
  }
  const active = String(config.active_provider || "");
  if (active && !object(config.providers)[active])
    errors.push({
      path: ["active_provider"],
      message: "请选择存在的 Provider",
    });
  for (const path of ["persona_file", "knowledge_files"]) {
    const values: Json[] =
      path === "persona_file"
        ? [config[path]]
        : Array.isArray(config[path])
          ? config[path]
          : [];
    if (path === "persona_file" && !config[path])
      errors.push({ path: [path], message: "人格文件路径不能为空" });
    if (
      values.some(
        (value) =>
          typeof value !== "string" ||
          value.includes("..") ||
          value.includes(":") ||
          value.startsWith("/") ||
          value.includes("\\"),
      )
    )
      errors.push({ path: [path], message: "文件路径必须在配置目录内" });
  }
  return errors;
}
