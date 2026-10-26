import { formatBytes } from "../lib/format";
import { getAt, object } from "../lib/types";
import type { Json, ObjectValue, Path } from "../lib/types";

export type FieldType =
  | "text"
  | "secret"
  | "number"
  | "bytes"
  | "toggle"
  | "select"
  | "segmented"
  | "tags"
  | "checks"
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
  help?: string;
  placeholder?: string;
  /** 与后端 ge / le / gt 约束保持一致。 */
  min?: number;
  max?: number;
  gt?: number;
  integer?: boolean;
  step?: number;
  unit?: string;
  bytes?: "decimal" | "binary";
  options?: Option[];
  nullable?: boolean;
  restart?: boolean;
  /** tags：输入内容按 JSON 解析为数字或布尔值。 */
  typed?: boolean;
  /** secret：提供随机生成。 */
  generate?: boolean;
  hidden?: (config: ObjectValue, base: Path) => boolean;
}
export interface SectionDef {
  id: string;
  title: string;
  description?: string;
  fields: FieldDef[];
}

const seconds = { unit: "秒", gt: 0 } as const;
const transport = (config: ObjectValue, base: Path) =>
  getAt(config, [...base, "transport"]);

export const napcatSection: SectionDef = {
  id: "napcat",
  title: "NapCat 接入",
  description: "NapCat 通过反向 WebSocket 连接到本服务。修改后需重启生效。",
  fields: [
    {
      key: "token",
      label: "连接密钥",
      type: "secret",
      generate: true,
      help: "须与 NapCat 反向 WebSocket 中填写的 Token 一致。留空时拒绝所有接入。",
    },
    {
      key: "expected_bot_id",
      label: "机器人 QQ 号",
      type: "text",
      placeholder: "例如 10001",
      help: "填写后只接受该账号接入；留空则接受任何持有正确密钥的机器人。",
    },
    {
      key: "action_timeout",
      label: "动作超时",
      type: "number",
      ...seconds,
      help: "等待 NapCat 回执的最长时间。超时的发送等操作结果未知，不会自动重试。",
    },
    {
      key: "media_timeout",
      label: "媒体读取超时",
      type: "number",
      ...seconds,
      help: "读取单个图片、语音、视频或文件的最长时间。",
    },
    {
      key: "media_max_bytes",
      label: "媒体大小上限",
      type: "bytes",
      bytes: "decimal",
      gt: 0,
      integer: true,
      help: "单个媒体的读取上限，也限制一次发送的媒体总量。",
    },
    {
      key: "media_concurrency",
      label: "媒体并发读取数",
      type: "number",
      min: 1,
      max: 32,
      integer: true,
      help: "同一批消息中同时读取的媒体数量。",
    },
  ],
};

export const serverSection: SectionDef = {
  id: "server",
  title: "控制台服务",
  description: "控制台、API 和 NapCat 接入共用这个地址。修改后需重启生效。",
  fields: [
    {
      key: "host",
      label: "监听地址",
      type: "text",
      help: "0.0.0.0 接受所有网卡的连接；只在本机使用时可填 127.0.0.1。",
    },
    {
      key: "port",
      label: "端口",
      type: "number",
      min: 1,
      max: 65535,
      integer: true,
    },
  ],
};

export const runtimeSection: SectionDef = {
  id: "runtime",
  title: "运行限制",
  description: "控制 Agent 每轮对话可使用的资源，不影响模型的生成参数。",
  fields: [
    {
      key: "concurrent_conversations",
      label: "同时处理的会话数",
      type: "number",
      min: 1,
      max: 32,
      integer: true,
      restart: true,
      help: "不同会话可以并行；同一会话的消息始终按顺序处理。",
    },
    {
      key: "max_model_rounds",
      label: "每轮模型请求上限",
      type: "number",
      min: 1,
      max: 100,
      integer: true,
      help: "一轮对话中发往模型接口的请求总数，包括自动压缩和超限恢复。",
    },
    {
      key: "turn_timeout",
      label: "单轮时限",
      type: "number",
      ...seconds,
      help: "超时即结束本轮；已经发出的消息不会撤回。",
    },
    {
      key: "history_count",
      label: "读取历史消息数",
      type: "number",
      min: 0,
      max: 300,
      integer: true,
      help: "每轮从 NapCat 读取的近期消息条数，0 表示不读取。",
    },
    {
      key: "max_pending_conversations",
      label: "排队会话上限",
      type: "number",
      min: 1,
      max: 1000,
      integer: true,
      help: "等待处理的会话超过上限后，新的唤醒会被丢弃并记录。",
    },
    {
      key: "max_pending_messages",
      label: "单会话排队消息上限",
      type: "number",
      min: 1,
      max: 100,
      integer: true,
      help: "处理期间收到的新唤醒会合并到下一轮，超过上限的被丢弃。",
    },
    {
      key: "tool_result_char_limit",
      label: "工具结果字符上限",
      type: "number",
      min: 1000,
      integer: true,
      unit: "字符",
      help: "超出部分会被截断，并告知模型结果不完整。",
    },
    {
      key: "activity_limit",
      label: "活动记录条数",
      type: "number",
      min: 10,
      max: 2000,
      integer: true,
      restart: true,
      help: "概览页保留的最近活动数量，只保存在内存中。",
    },
  ],
};

export const providerApiSection: SectionDef = {
  id: "provider-api",
  title: "接口",
  fields: [
    {
      key: "protocol",
      label: "接口协议",
      type: "segmented",
      options: [
        { value: "responses", label: "Responses" },
        { value: "chat_completions", label: "Chat Completions" },
      ],
      help: "按服务商实际支持的 OpenAI 兼容协议选择。",
    },
    {
      key: "base_url",
      label: "接口地址",
      type: "text",
      placeholder: "https://api.openai.com/v1",
      help: "OpenAI 兼容接口的根地址，通常以 /v1 结尾。",
    },
    {
      key: "api_key",
      label: "API Key",
      type: "secret",
      help: "只在服务端用于请求模型，不会发给 Agent。",
    },
    {
      key: "proxy",
      label: "HTTP 代理",
      type: "text",
      nullable: true,
      placeholder: "http://127.0.0.1:7890",
      help: "留空表示直连。",
    },
  ],
};

export const providerModelSection: SectionDef = {
  id: "provider-model",
  title: "模型",
  fields: [
    {
      key: "model",
      label: "模型名称",
      type: "model",
      help: "列表来自接口返回的模型；读取失败时可以直接输入。",
    },
    {
      key: "reasoning_effort",
      label: "思考等级",
      type: "select",
      nullable: true,
      placeholder: "不发送（使用接口默认）",
      options: ["none", "minimal", "low", "medium", "high", "xhigh", "max"].map(
        (value) => ({ value, label: value }),
      ),
      help: "只在模型支持时选择。不支持的取值会让请求直接失败，不会自动降级。",
    },
    {
      key: "max_context_tokens",
      label: "上下文窗口",
      type: "number",
      min: 4096,
      integer: true,
      unit: "token",
      help: "填写模型的总上下文长度，不是回复长度上限。",
    },
    {
      key: "modalities",
      label: "支持的输入类型",
      type: "checks",
      options: [
        { value: "text", label: "文本" },
        { value: "image", label: "图片" },
        { value: "audio", label: "音频" },
        { value: "video", label: "视频" },
      ],
      help: "只勾选模型实际支持的类型；收到不支持的媒体时会明确报错。",
    },
  ],
};

export const providerRequestSection: SectionDef = {
  id: "provider-request",
  title: "请求",
  fields: [
    {
      key: "timeout",
      label: "请求超时",
      type: "number",
      ...seconds,
      help: "单次模型请求的最长等待时间。",
    },
    {
      key: "store_supported",
      label: "请求附带 store: false",
      type: "toggle",
      help: "要求服务端不保存对话记录。接口不认识该参数时请关闭。",
    },
  ],
};

export const wakeSection: SectionDef = {
  id: "wake",
  title: "唤醒",
  fields: [
    {
      key: "keywords",
      label: "唤醒关键词",
      type: "tags",
      placeholder: "输入关键词后按回车",
      help: "消息包含任一关键词时唤醒。@机器人、回复机器人的消息总会唤醒。",
      hidden: (_config, base) => base[0] === "private",
    },
  ],
};

export const limitsSection: SectionDef = {
  id: "limits",
  title: "发送与跨会话",
  fields: [
    {
      key: "max_sends_per_turn",
      label: "每轮最多发送",
      type: "number",
      min: 1,
      max: 100,
      integer: true,
      unit: "次",
      help: "一轮对话里发送消息的次数上限，文件和合并转发各算一次。",
    },
    {
      key: "allow_at_all",
      label: "允许 @全体成员",
      type: "toggle",
      help: "仍受 QQ 平台的次数限制。",
      hidden: (_config, base) => base[0] === "private",
    },
    {
      key: "cross_private",
      label: "允许私聊其他用户",
      type: "toggle",
      help: "开启后，Agent 可以向当前会话以外的 QQ 用户发私聊。",
    },
    {
      key: "cross_groups",
      label: "可操作的其他群",
      type: "tags",
      placeholder: "输入群号后按回车",
      help: "默认只作用于当前会话。列出的群可作为发送消息和已开放操作的目标。",
    },
  ],
};

export const mcpConnectionSection: SectionDef = {
  id: "mcp-connection",
  title: "连接",
  description: "MCP 服务的所有设置都在重启 Harness 后生效。",
  fields: [
    {
      key: "transport",
      label: "传输方式",
      type: "segmented",
      options: [
        { value: "streamable_http", label: "Streamable HTTP" },
        { value: "stdio", label: "stdio" },
      ],
    },
    {
      key: "url",
      label: "服务地址",
      type: "text",
      placeholder: "https://example.com/mcp",
      hidden: (config, base) => transport(config, base) !== "streamable_http",
    },
    {
      key: "command",
      label: "启动命令",
      type: "text",
      placeholder: "npx",
      help: "由服务端启动的本地程序，Agent 无法修改。",
      hidden: (config, base) => transport(config, base) !== "stdio",
    },
    {
      key: "args",
      label: "命令参数",
      type: "tags",
      placeholder: "输入一个参数后按回车",
      help: "每项作为一个独立参数传入。",
      hidden: (config, base) => transport(config, base) !== "stdio",
    },
    {
      key: "timeout",
      label: "调用超时",
      type: "number",
      ...seconds,
      help: "单次工具调用的最长等待时间；超时不会自动重试。",
    },
  ],
};

export const mcpSecretsSection: SectionDef = {
  id: "mcp-secrets",
  title: "认证与环境",
  fields: [
    {
      key: "headers",
      label: "HTTP 请求头",
      type: "map",
      help: "例如认证信息。只用于连接，不会发给 Agent。",
      hidden: (config, base) => transport(config, base) !== "streamable_http",
    },
    {
      key: "env",
      label: "环境变量",
      type: "map",
      help: "只传给这个 MCP 进程，不会发给 Agent。",
      hidden: (config, base) => transport(config, base) !== "stdio",
    },
  ],
};

export const mcpToolsSection: SectionDef = {
  id: "mcp-tools",
  title: "工具",
  fields: [
    {
      key: "read_only_tools",
      label: "只读工具",
      type: "tags",
      placeholder: "输入远端工具名后按回车",
      help: "标记为只读的工具调用失败后允许 Agent 重试。能否调用仍由会话权限决定。",
    },
  ],
};

export const sandboxSection: SectionDef = {
  id: "sandbox",
  title: "资源与连接",
  description: "修改后需要重新创建 Runner 并重启 Harness 才会生效。",
  fields: [
    {
      key: "runner_url",
      label: "Runner 地址",
      type: "text",
      help: "沙箱控制器的内部地址，执行代码的容器无法访问。",
    },
    {
      key: "runner_token",
      label: "Runner 密钥",
      type: "secret",
      generate: true,
      help: "Harness 与 Runner 之间的认证密钥，不会注入执行容器。",
    },
    {
      key: "quota_bytes",
      label: "共享空间配额",
      type: "bytes",
      bytes: "decimal",
      min: 1_000_000,
      integer: true,
      help: "须与工作区 Btrfs 子卷的硬配额一致，否则沙箱拒绝运行。",
    },
    {
      key: "memory_bytes",
      label: "内存上限",
      type: "bytes",
      bytes: "binary",
      min: 134_217_728,
      integer: true,
      help: "代码执行可用的内存上限。",
    },
    {
      key: "cpus",
      label: "CPU 上限",
      type: "number",
      gt: 0,
      step: 0.1,
      unit: "核",
      help: "所有代码执行共享。",
    },
    {
      key: "pids_limit",
      label: "进程数上限",
      type: "number",
      min: 16,
      integer: true,
      help: "限制一次执行中可以创建的进程数。",
    },
    {
      key: "command_timeout",
      label: "单次执行时限",
      type: "number",
      ...seconds,
      max: 600,
      help: "超时会终止执行，已写入的文件保留。",
    },
    {
      key: "output_limit",
      label: "输出上限",
      type: "bytes",
      bytes: "binary",
      min: 1024,
      max: 16_777_216,
      integer: true,
      help: "单次执行返回的终端输出上限，超出部分截断。",
    },
  ],
};

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

export interface FieldInstance {
  field: FieldDef;
  path: Path;
  section: SectionDef;
  /** 字段所在页面，用于搜索跳转和错误定位。 */
  route: string;
}
const enc = encodeURIComponent;
export function fieldInstances(config: ObjectValue): FieldInstance[] {
  const output: FieldInstance[] = [];
  const add = (section: SectionDef, base: Path, route: string) =>
    section.fields.forEach((field) => {
      if (!field.hidden?.(config, base))
        output.push({ field, path: [...base, field.key], section, route });
    });
  add(napcatSection, ["napcat"], "/settings/napcat");
  add(runtimeSection, ["runtime"], "/settings/runtime");
  add(serverSection, ["server"], "/settings/server");
  add(sandboxSection, ["sandbox"], "/sandbox/settings");
  for (const id of Object.keys(object(config.providers))) {
    const route = "/models/" + enc(id);
    add(providerApiSection, ["providers", id], route);
    add(providerModelSection, ["providers", id], route);
    add(providerRequestSection, ["providers", id], route);
  }
  for (const id of Object.keys(object(config.mcp))) {
    const route = "/tools/mcp/" + enc(id);
    add(mcpConnectionSection, ["mcp", id], route);
    add(mcpSecretsSection, ["mcp", id], route);
    add(mcpToolsSection, ["mcp", id], route);
  }
  const conversations: [Path, string][] = [
    [["private"], "/conversations/private"],
    ...Object.keys(object(config.groups)).map(
      (id) => [["groups", id], "/conversations/group/" + enc(id)] as [Path, string],
    ),
  ];
  for (const [base, route] of conversations) {
    add(wakeSection, base, route);
    add(limitsSection, [...base, "policy"], route);
  }
  return output;
}

/** 配置路径对应的页面；用于错误提示和保存记录的跳转。 */
export function routeForPath(path: Path): string {
  const [head, id] = path;
  if (head === "groups" && id !== undefined)
    return (
      "/conversations/group/" +
      enc(String(id)) +
      (path[2] === "policy" && ["categories", "tools", "parameters"].includes(String(path[3]))
        ? "?tab=permissions"
        : "")
    );
  if (head === "private")
    return (
      "/conversations/private" +
      (path[1] === "policy" && ["categories", "tools", "parameters"].includes(String(path[2]))
        ? "?tab=permissions"
        : "")
    );
  if (head === "providers" && id !== undefined) return "/models/" + enc(String(id));
  if (head === "active_provider") return "/models";
  if (head === "mcp" && id !== undefined) return "/tools/mcp/" + enc(String(id));
  if (head === "persona_file" || head === "knowledge_files") return "/prompts";
  if (head === "sandbox") return "/sandbox/settings";
  if (head === "napcat") return "/settings/napcat";
  if (head === "runtime") return "/settings/runtime";
  if (head === "server") return "/settings/server";
  return "/settings/config-file";
}

export function fieldId(path: Path): string {
  return "field-" + path.map((part) => enc(String(part))).join("--");
}

const conversationParts: Record<string, string> = {
  enabled: "启用",
  categories: "能力权限",
  tools: "工具权限",
  parameters: "参数限制",
};
function scoped(prefix: string, rest: Path, name: string | undefined) {
  if (name) return prefix + " · " + name;
  const key = String(rest[0] === "policy" ? (rest[1] ?? "") : (rest[0] ?? ""));
  return conversationParts[key] ? prefix + " · " + conversationParts[key] : prefix;
}

/** 保存记录与错误提示中显示的字段名，带上所属会话或连接。 */
export function fieldLabel(config: ObjectValue, path: Path): string {
  const key = path.join("\u0000");
  const name = fieldInstances(config).find((item) => item.path.join("\u0000") === key)
    ?.field.label;
  const [head, id] = path;
  if (head === "groups") return scoped("群 " + id, path.slice(2), name);
  if (head === "private") return scoped("私聊", path.slice(1), name);
  if (head === "providers") return scoped("模型连接 " + id, [], name);
  if (head === "mcp") return scoped("MCP " + id, path.slice(2), name);
  if (name) return name;
  if (head === "active_provider") return "当前模型连接";
  if (head === "persona_file") return "人格文件";
  if (head === "knowledge_files") return "知识库文件";
  return path.join(".");
}

function limit(field: FieldDef, value: number) {
  return field.type === "bytes" ? formatBytes(value, field.bytes) : String(value);
}

export function validateClient(config: ObjectValue): { path: Path; message: string }[] {
  const errors: { path: Path; message: string }[] = [];
  for (const { field, path } of fieldInstances(config)) {
    if (field.type !== "number" && field.type !== "bytes") continue;
    const value: Json | undefined = getAt(config, path);
    if (value === null && field.nullable) continue;
    let message = "";
    if (typeof value !== "number" || !Number.isFinite(value)) message = "请填写数字";
    else if (field.integer && !Number.isInteger(value)) message = "须为整数";
    else if (field.gt !== undefined && value <= field.gt)
      message = "须大于 " + limit(field, field.gt);
    else if (field.min !== undefined && value < field.min)
      message = "最小为 " + limit(field, field.min);
    else if (field.max !== undefined && value > field.max)
      message = "最大为 " + limit(field, field.max);
    if (message) errors.push({ path, message });
  }
  const policies: [Path, Json | undefined][] = [
    [["private", "policy"], object(config.private).policy],
    ...Object.entries(object(config.groups)).map(
      ([id, value]) => [["groups", id, "policy"], object(value).policy] as [Path, Json | undefined],
    ),
  ];
  for (const [base, policy] of policies)
    for (const [tool, rules] of Object.entries(object(object(policy).parameters)))
      for (const [parameter, raw] of Object.entries(object(rules))) {
        const rule = object(raw);
        const path = [...base, "parameters", tool, parameter];
        if (
          typeof rule.minimum === "number" &&
          typeof rule.maximum === "number" &&
          rule.minimum > rule.maximum
        )
          errors.push({ path: [...path, "maximum"], message: "最大值不能小于最小值" });
        const length = rule.max_length;
        if (
          length !== undefined &&
          length !== null &&
          (typeof length !== "number" || !Number.isInteger(length) || length < 0)
        )
          errors.push({ path: [...path, "max_length"], message: "最大长度须为不小于 0 的整数" });
      }
  const active = String(config.active_provider || "");
  if (active && !Object.hasOwn(object(config.providers), active))
    errors.push({ path: ["active_provider"], message: "当前模型连接不存在，请重新选择" });
  const persona = config.persona_file;
  if (typeof persona !== "string" || !persona.trim())
    errors.push({ path: ["persona_file"], message: "请选择人格文件" });
  const paths: [Path, Json][] = [
    [["persona_file"], persona ?? null],
    ...(Array.isArray(config.knowledge_files) ? config.knowledge_files : []).map(
      (value, index) => [["knowledge_files", index], value] as [Path, Json],
    ),
  ];
  for (const [path, value] of paths)
    if (
      typeof value === "string" &&
      (value.includes("..") || value.includes(":") || value.startsWith("/") || value.includes("\\"))
    )
      errors.push({ path, message: "文件必须位于配置目录内" });
  return errors;
}
