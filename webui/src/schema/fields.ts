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
  | "provider"
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
  /** provider：空值也是有效选择时的说明，例如“使用会话模型”。 */
  emptyLabel?: string;
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
      help: "每次媒体下载或 NapCat 刷新的等待时间；一种来源失败后仍可尝试其他来源。",
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
      integer: true,
      help: "同一批消息中同时读取的媒体数量。",
    },
    { key: "event_queue_size", label: "接入事件队列", type: "number", min: 1, integer: true,
      help: "接入队列容量；溢出计数显示在概览页。" },
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
      integer: true,
      restart: true,
      help: "不同会话可以并行；同一会话的消息始终按顺序处理。",
    },
    {
      key: "max_model_rounds",
      label: "每轮模型请求上限",
      type: "number",
      min: 1,
      integer: true,
      help: "一轮对话的正常模型请求上限；压缩请求独立使用相同数量的额度。失败重试单独计数，不消耗此额度。",
    },
    {
      key: "history_count",
      label: "读取历史消息数",
      type: "number",
      min: 0,
      integer: true,
      help: "每轮从 NapCat 读取的近期消息条数，0 表示不读取。",
    },
    {
      key: "max_pending_conversations",
      label: "排队会话上限",
      type: "number",
      min: 1,
      integer: true,
      help: "等待处理的会话超过上限后，新的唤醒会被丢弃并记录。",
    },
    {
      key: "max_pending_messages",
      label: "单会话排队消息上限",
      type: "number",
      min: 1,
      integer: true,
      help: "处理期间收到的新唤醒会合并到下一轮，超过上限的被丢弃。",
    },
    {
      key: "activity_limit",
      label: "活动记录条数",
      type: "number",
      min: 1,
      integer: true,
      restart: true,
      help: "概览页保留的最近活动数量，只保存在内存中。",
    },
    { key: "max_retained_sessions", label: "保留会话数", type: "number", min: 1, integer: true,
      help: "超过容量后回收最久未使用的空闲会话，并记录事件。" },
    { key: "max_retained_bytes", label: "会话内存预算", type: "bytes", bytes: "binary", min: 1, integer: true,
      help: "上下文、知识资料和未知动作参数的合计保留预算。" },
    { key: "config_text_max_bytes", label: "配置文本编辑预算", type: "bytes", bytes: "decimal", min: 1, integer: true,
      help: "人格、知识库和 TOML 编辑器的单文件读写预算；需要编辑大文件时可提高。" },
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

export const providerRequestSection: SectionDef = {
  id: "provider-request",
  title: "请求",
  fields: [
    {
      key: "timeout",
      label: "请求超时",
      type: "number",
      ...seconds,
      help: "每次模型请求的最长等待时间，包含响应正文接收；超时后按重试次数设置重新请求。",
    },
    {
      key: "max_retries",
      label: "失败重试次数",
      type: "number",
      min: 0,
      integer: true,
      help: "首次请求失败后最多重试的次数，默认 10 次（合计最多 11 次请求）；0 表示不重试。用于临时服务错误、限流、连接异常和超时。",
    },
    {
      key: "store_supported",
      label: "请求附带 store: false",
      type: "toggle",
      help: "要求服务端不保存对话记录。接口不认识该参数时请关闭。",
    },
    {
      key: "headers",
      label: "自定义请求头",
      type: "map",
      placeholder: "请求头名称",
      help: "调用模型和拉取模型列表时附带的 HTTP 请求头。只用于请求接口，不会发给 Agent。",
    },
  ],
};

const reasoningField: FieldDef = {
  key: "reasoning_effort",
  label: "思考等级",
  type: "select",
  nullable: true,
  placeholder: "不发送（使用接口默认）",
  options: ["none", "minimal", "low", "medium", "high", "xhigh", "max"].map((value) => ({
    value,
    label: value,
  })),
  help: "只在模型支持时选择。不支持的取值会让请求直接失败，不会自动降级。",
};

export const sessionModelSection: SectionDef = {
  id: "session-model",
  title: "会话模型",
  description: "这个会话回复时使用的模型。",
  fields: [
    { key: "provider", label: "服务商", type: "provider", placeholder: "选择服务商" },
    {
      key: "name",
      label: "模型名称",
      type: "model",
      help: "列表来自服务商接口；读取失败时可以直接输入。",
    },
    reasoningField,
    {
      key: "max_context_tokens",
      label: "上下文预算",
      type: "number",
      min: 4096,
      integer: true,
      unit: "token",
      help: "不要超过模型的总上下文长度。设得更小会让会话更早压缩，每次唤醒发送的内容也更少。",
    },
    {
      key: "compaction_target_percent",
      label: "压缩目标",
      type: "number",
      min: 10,
      max: 70,
      integer: true,
      unit: "%",
      help: "上下文达到预算的 80% 时，用会话模型把旧资料压缩到预算的这个比例。",
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

const idleOff = (config: ObjectValue, base: Path) => getAt(config, [...base, "enabled"]) !== true;

export const idleCompactionSection: SectionDef = {
  id: "idle-compaction",
  title: "闲时压缩",
  description:
    "会话闲置后，把人格和知识库以外的全部内容压缩成一份摘要，不保留原文。服务商的缓存过期后，再次唤醒只需按摘要计费。",
  fields: [
    {
      key: "enabled",
      label: "启用闲时压缩",
      type: "toggle",
      help: "压缩进行中收到新消息时，会立即中断压缩并保留原上下文直接回复。",
    },
    {
      key: "after_minutes",
      label: "闲置多久后压缩",
      type: "number",
      min: 1,
      max: 10080,
      integer: true,
      unit: "分钟",
      help: "从一轮结束时开始计时，每段闲置只压缩一次。建议略长于服务商的缓存保留时间。",
      hidden: idleOff,
    },
    {
      key: "min_tokens",
      label: "最少上下文",
      type: "number",
      min: 0,
      integer: true,
      unit: "token",
      help: "上下文低于这个量时不压缩，避免为很短的对话付压缩费用。",
      hidden: idleOff,
    },
  ],
};

const compactorOff = (config: ObjectValue, base: Path) => idleOff(config, base.slice(0, -1));
const ownCompactorOff = (config: ObjectValue, base: Path) =>
  compactorOff(config, base) || !getAt(config, [...base, "provider"]);

export const compactionModelSection: SectionDef = {
  id: "compaction-model",
  title: "压缩模型",
  description: "生成闲时压缩摘要的模型，可以选用比会话模型便宜的模型。",
  fields: [
    {
      key: "provider",
      label: "服务商",
      type: "provider",
      emptyLabel: "使用会话模型",
      help: "选择“使用会话模型”时，摘要请求不发送思考等级。",
      hidden: compactorOff,
    },
    { key: "name", label: "模型名称", type: "model", hidden: ownCompactorOff },
    { ...reasoningField, hidden: ownCompactorOff },
    {
      key: "max_context_tokens",
      label: "上下文窗口",
      type: "number",
      min: 4096,
      integer: true,
      unit: "token",
      help: "压缩模型的总上下文长度；会话内容超过时分段摘要。",
      hidden: ownCompactorOff,
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
      help: "明确声明只读工具；未声明时参考服务的只读注解。调用权限仍由会话策略决定。",
    },
    { key: "effect_tools", label: "有副作用的工具", type: "tags",
      help: "强制按副作用处理，优先于服务的只读注解和只读名单。" },
  ],
};

export const imageConnectionSection: SectionDef = {
  id: "image-connection",
  title: "生图接口与模型",
  description:
    "独立于会话服务商的 OpenAI 标准生图接口（自动支持 /images/generations 文生图与 /images/edits 图生图）。保存后立即生效。",
  fields: [
    {
      key: "enabled",
      label: "启用图像生成",
      type: "toggle",
      help: "启用并选好模型后，在会话的「能力权限」中开放「生成图片」即可由 Agent 自主调用生图或改图。",
    },
    {
      key: "base_url",
      label: "接口地址",
      type: "text",
      placeholder: "https://api.openai.com/v1",
      help: "OpenAI 兼容生图接口的根地址，通常以 /v1 结尾。",
    },
    {
      key: "api_key",
      label: "API Key",
      type: "secret",
      help: "只在服务端用于请求生图接口，不会发给 Agent。",
    },
    {
      key: "model",
      label: "模型名称",
      type: "model",
      help: "列表来自生图接口；读取失败或接口不提供模型列表时可直接输入。",
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

export const imageRequestSection: SectionDef = {
  id: "image-request",
  title: "请求与重试",
  fields: [
    {
      key: "timeout",
      label: "请求超时",
      type: "number",
      ...seconds,
      help: "每次生图请求的最长等待时间；超时后按重试次数设置重新请求。",
    },
    {
      key: "max_retries",
      label: "失败重试次数",
      type: "number",
      min: 0,
      integer: true,
      help: "首次请求失败后最多重试的次数，默认 3 次；0 表示不重试。",
    },
    {
      key: "headers",
      label: "自定义请求头",
      type: "map",
      placeholder: "请求头名称",
      help: "调用生图接口和拉取模型列表时附带的 HTTP 请求头。只用于请求接口，不会发给 Agent。",
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
      help: "超时会终止执行，已写入的文件保留。",
    },
    {
      key: "output_limit",
      label: "输出上限",
      type: "bytes",
      bytes: "binary",
      min: 1,
      integer: true,
      help: "返回到模型的输出预览预算；超出时完整输出保存在工作区，可继续分段读取。",
    },
    { key: "transfer_max_bytes", label: "单次文件传输预算", type: "bytes", bytes: "decimal", min: 1, integer: true,
      help: "二进制文件传输与文本写入共用此预算；文本可分段读取或追加。" },
    { key: "max_pending_operations", label: "工作区等待操作上限", type: "number", min: 1, integer: true,
      help: "执行与文件操作在有界内存中等待；队列满时明确告知未执行，可稍后重试。" },
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
export function defaultSessionModel(): ObjectValue {
  return {
    provider: "",
    name: "",
    reasoning_effort: null,
    max_context_tokens: 128000,
    compaction_target_percent: 50,
    modalities: ["text", "image"],
  };
}
export function defaultIdleCompaction(): ObjectValue {
  return {
    enabled: false,
    after_minutes: 10,
    min_tokens: 16000,
    model: { provider: "", name: "", reasoning_effort: null, max_context_tokens: 128000 },
  };
}
export function defaultConversation(): ObjectValue {
  return {
    enabled: false,
    keywords: [],
    model: defaultSessionModel(),
    idle_compaction: defaultIdleCompaction(),
    policy: defaultPolicy(),
  };
}
export function defaultProvider(): ObjectValue {
  return {
    protocol: "responses",
    base_url: "https://api.openai.com/v1",
    api_key: "",
    headers: {},
    timeout: 120,
    max_retries: 10,
    proxy: null,
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
    effect_tools: [],
  };
}

/** 私聊和各群在配置中的路径与内容；私聊所有对象共用一套。 */
export function conversationEntries(config: ObjectValue): [Path, ObjectValue][] {
  return [
    [["private"], object(config.private)],
    ...Object.entries(object(config.groups)).map(
      ([id, value]) => [["groups", id], object(value)] as [Path, ObjectValue],
    ),
  ];
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
  add(imageConnectionSection, ["image"], "/tools/image");
  add(imageRequestSection, ["image"], "/tools/image");
  for (const id of Object.keys(object(config.providers))) {
    const route = "/providers/" + enc(id);
    add(providerApiSection, ["providers", id], route);
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
    add(sessionModelSection, [...base, "model"], route + "?tab=model");
    add(idleCompactionSection, [...base, "idle_compaction"], route + "?tab=model");
    add(compactionModelSection, [...base, "idle_compaction", "model"], route + "?tab=model");
  }
  return output;
}

/** 会话内路径所在的标签页查询串。 */
function conversationTab(rest: Path): string {
  const [section, key] = rest.map(String);
  if (section === "model" || section === "idle_compaction") return "?tab=model";
  if (section === "policy" && ["categories", "tools", "parameters"].includes(key))
    return "?tab=permissions";
  return "";
}

/** 配置路径对应的页面；用于错误提示和保存记录的跳转。 */
export function routeForPath(path: Path): string {
  const [head, id] = path;
  if (head === "groups" && id !== undefined)
    return "/conversations/group/" + enc(String(id)) + conversationTab(path.slice(2));
  if (head === "private") return "/conversations/private" + conversationTab(path.slice(1));
  if (head === "providers" && id !== undefined) return "/providers/" + enc(String(id));
  if (head === "image") return "/tools/image";
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
  model: "会话模型",
  idle_compaction: "闲时压缩",
  headers: "自定义请求头",
  env: "环境变量",
};
function scoped(prefix: string, rest: Path, name: string | undefined) {
  // 会话模型和压缩模型的字段同名，带上所属部分以便区分。
  const part =
    rest[0] === "model"
      ? "会话模型"
      : rest[0] === "idle_compaction" && rest[1] === "model"
        ? "压缩模型"
        : "";
  if (name) return prefix + (part ? " · " + part : "") + " · " + name;
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
  if (head === "providers") return scoped("服务商 " + id, path.slice(2), name);
  if (head === "image") return scoped("图像生成", path.slice(1), name);
  if (head === "mcp") return scoped("MCP " + id, path.slice(2), name);
  if (name) return name;
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
  const providers = object(config.providers);
  for (const [base, conversation] of conversationEntries(config))
    for (const part of [["model"], ["idle_compaction", "model"]]) {
      const provider = getAt(conversation, [...part, "provider"]);
      if (typeof provider === "string" && provider && !Object.hasOwn(providers, provider))
        errors.push({ path: [...base, ...part, "provider"], message: "所选服务商不存在，请重新选择" });
    }
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
      (value.split("/").includes("..") || value.includes(":") || value.startsWith("/") || value.includes("\\"))
    )
      errors.push({ path, message: "文件必须位于配置目录内" });
  return errors;
}
