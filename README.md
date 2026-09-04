# MyBot

MyBot 是面向 QQ 群聊的机器人服务。它通过 FastAPI 接收 NapCat 反向 WebSocket 事件，使用 PostgreSQL 保存群消息、撤回记录和群图片，并通过 OpenAI Chat Completions 协议连接语言模型。

项目要求 Python 3.13+，依赖由 `uv` 管理。

## 主要功能

- 按事件类型把 NapCat 消息交给内置插件。
- 保存入站和出站群消息；撤回后普通查询不可见，但原文和图片仍保留。
- 提供 AI 群聊、上下文压缩、视觉描述、MCP 和 NapCat 群聊工具。
- 用唯一配置文件管理服务、模型和插件；插件配置和引用的文本文件支持自动热加载。
- 提供同端口 WebUI，用表单编辑配置和 prompt，并自动校验、保存改动。
- 解析模型回复中的 `<Reply>` 与 `<At>` 标记，并转换为 NapCat 消息段。

## 本地运行

```bash
uv sync
mkdir config
cp config.example.toml config/mybot.toml
```

编辑 `config/mybot.toml`，并把其中引用的 prompt、知识库文件放到 `config/` 下。随后启动 PostgreSQL、执行 migration 并运行服务：

```bash
uv run python -m app.database.migrations upgrade
uv run python -m app.main
```

NapCat 反向 WebSocket 地址通常是：

```text
ws://<本机局域网 IP>:6055/ws/napcat
```

配置 `[napcat].websocket_token` 时校验 Bearer Token；省略或留空时不校验。

## WebUI

主服务会在 `http://<主机>:6055/` 提供配置控制台，API 与页面使用同一端口。控制台面向可信内网，不含登录功能，并会明文读取和写回配置中的密钥。

前端开发时分别启动后端和 Vite：

```bash
uv run python -m app.webui.dev
cd webui
npm ci
npm run dev
```

开发页面位于 `http://127.0.0.1:5173/`，Vite 会把 `/api` 转发到 6056。配置字段停止编辑 800ms 后自动校验和保存，文本文件停止编辑 1 秒后自动保存；非法值、空的必填 prompt 和内容哈希冲突都不会覆盖磁盘文件。文件页允许读写 `config/` 内任意扩展名的 UTF-8 文本，包括 `mybot.toml`；TOML 语法损坏时可直接在该页修复原文。

顶栏保存与 `Ctrl+S` 保存当前编辑的配置或文本文件。切换页面会保留已打开的文本编辑会话，并继续自动保存；文件冲突可选择覆盖、丢弃并刷新或继续编辑。配置或文本仍有待保存内容时，重启和关机按钮会等待保存完成，关闭浏览器页面也会提示未保存内容。

## Docker

Compose 使用 `postgres:18.4-bookworm`，PostgreSQL 不向宿主机公开端口。容器内配置必须使用 `database.host = "postgres"` 和 `database.password_file = "/run/secrets/postgres_password"`。

```bash
umask 077
mkdir -p config secrets images logs
cp config.example.toml config/mybot.toml
# 编辑 config/mybot.toml，并向 secrets/postgres_password 写入数据库密码。
docker compose up -d
```

主要挂载如下：

- `./config:/app/config:rw`（MyBot 在线保存配置）
- `./images:/app/images`
- `./logs:/app/logs`
- `mybot-postgres-data:/var/lib/postgresql`

`migrate` 会等待 PostgreSQL 健康后执行 migration，成功后 MyBot 才启动。应用启动时只检查 migration 版本，不会自动修改 schema。数据库和图片没有自动过期或备份机制。

默认 Compose 允许 MyBot 通过 WebUI 在线保存 `config/`，`migrate` 服务仍保持只读挂载。WebUI 面向可信内网使用，不应直接暴露到公网。

## 配置

唯一配置文件是 `config/mybot.toml`，完整字段见 [config.example.toml](config.example.toml)。所有配置模型都禁止未知字段。

运行参数必须在配置中明确填写。项目不会在运行时用常量补齐缺失的超时、重试、并发、数量或大小限制；字段缺失会在启动或 WebUI 校验时返回完整字段路径。可关闭的限制使用文档中声明的 `0` 或空列表语义，不依赖省略字段。

插件消费者数量和优先级使用 `[plugin_execution.plugins.<plugin_id>]` 动态映射。启动时会比较已加载插件与配置键；缺少执行配置或配置了不存在的插件都会直接报出对应 ID。

### 热加载范围

应用自动监听 `config/`，连续文件变化会合并处理：

- `[plugins.*]`、这些配置引用的 prompt、知识库和通用要求文件会热加载。
- 插件配置节存在即启用；删除该节即停用。当前事件继续使用取得时的旧配置，下一条相关事件使用新配置。
- TOML 不完整、字段无效或引用文件不可读时，整次重载失败，现有配置继续生效。
- `[app]`、`[server]`、`[napcat]`、`[storage]`、`[network]`、`[logging]`、`[llm]`、`[mcp]`、`[database]` 和 `[plugin_execution]` 只在启动时生效。运行中修改这些节会记录需要重启，但同一次保存中的有效插件变化仍会应用。
- 新增 LLM provider 必须重启。热加载期间，插件只能引用进程启动时已经注册的 provider。

配置引用文件必须使用相对于 `config/` 的路径。绝对路径、越出目录的 `..` 和指向目录外的符号链接都会被拒绝。system、vision 和通用要求文件不能为空；知识库可以省略或留空。

### LLM provider 与模型引用

Provider ID 直接使用表名：

```toml
[llm.providers.deepseek]
api_key = "sk-CHANGE_ME"
base_url = "https://api.deepseek.com"
inherit_network_proxy = true
timeout_seconds = 0
max_attempts = 5
retry_delay_seconds = 0
retry_max_delay_seconds = 10
```

无鉴权的 OpenAI 兼容服务可以省略 `api_key`。未配置 key 时不会发送 `Authorization` 请求头。`timeout_seconds = 0` 表示使用 `[network].timeout_seconds`；`retry_max_delay_seconds = 0` 表示指数退避不设最大等待时间。Provider 可单独配置 `proxy`，也可通过 `inherit_network_proxy` 决定是否继承全局代理。

插件使用 `{ provider, name }` 引用模型，例如：

```toml
model = { provider = "deepseek", name = "deepseek-chat" }
```

### MCP

MCP server 使用 stdio 启动，工具名会转换为 `mcp__{server}__{tool}`：

```toml
[mcp]
enabled = true
initialization_timeout_seconds = 30
call_timeout_seconds = 60

[mcp.servers.example]
command = "npx"
args = ["-y", "your-mcp-server"]
env = { EXAMPLE_API_KEY = "CHANGE_ME" }
disabled = false
```

MCP 命令、环境变量和密钥属于部署配置，不应提交到仓库。
`initialization_timeout_seconds` 限制每个服务握手与工具清单加载的总等待时间；初始化失败会释放已启动的 MCP 资源。`call_timeout_seconds` 限制单次工具调用，超时会把结构化错误返回给 AI，后续调用仍可继续。两个字段均须明确填写正数。
镜像内已包含 `/app/node_modules/.bin/firecrawl-mcp`、`/app/node_modules/.bin/mcp-searxng` 和 `/usr/local/bin/github-mcp-server`，部署配置可以直接使用这些 stdio 命令，不需要运行时下载安装。

### AI 群聊与图片交付

AI 群聊始终由主模型生成正式回复。`model.supports_images` 声明主模型能力，`images.delivery_mode` 决定图片实际走向：

- `direct`：原始图片字节直接进入主模型的 Chat Completions 请求，不调用其他模型，也不缩放或重新编码。
- `vision`：独立视觉模型只接收当前问题、图片和视觉提示词，生成事实描述后交给主模型；它不接收角色、历史或工具。

`direct` 要求主模型支持图片。`vision` 要求配置 `[plugins.ai_group_chat.vision]`。原生多模态模式仍可保留 vision，仅在 `oversize_behavior = "describe"` 时把超过主模型已声明限制的图片交给视觉模型；选择 `error` 或 `skip` 时不会发生模型转发。

```toml
[plugins.ai_group_chat]
model = { provider = "deepseek", name = "deepseek-chat", supports_images = false }
extra_requirements_file = "ai_group_chat/prompts/extra_requirements.md"
show_reasoning = false
retain_reasoning = false
tool_result_retention = "off"

[plugins.ai_group_chat.vision]
model = { provider = "vision", name = "vision-model" }
system_prompt_file = "ai_group_chat/prompts/vision/system.md"
user_prompt_file = "ai_group_chat/prompts/vision/user.md"
max_attempts = 5
retry_delay_seconds = 0.25
retry_max_delay_seconds = 10
retain_descriptions = true

[plugins.ai_group_chat.images]
delivery_mode = "vision"
max_per_turn = 0
max_image_bytes = 0
max_total_bytes_per_request = 0
max_width = 0
max_height = 0
allowed_mime_types = []
oversize_behavior = "skip"
image_detail = "auto" # 可设为 omit，完全不发送 detail 字段
retain_images = false
forward_max_per_call = 0
forward_max_per_turn = 0

[[plugins.ai_group_chat.groups]]
id = "123456789"
system_prompt_file = "ai_group_chat/prompts/roles/default.md"
knowledge_base_file = "ai_group_chat/knowledge/default.md"
max_context_tokens = 64000
```

图片读取依次尝试已有路径、现有 URL 和 NapCat `get_image` 刷新。图片数量、单图原始字节、单次请求图片总原始字节、宽高、MIME 类型、下载并发、超时及转发图片额度都由配置决定；数量、字节和宽高字段为 `0` 时不限，MIME 列表为空时不限制格式。超过正数限制时按 `oversize_behavior` 执行：`error` 立即结束本轮并报错，`skip` 跳过单图并把原因告诉主模型，`describe` 调用明确配置的视觉模型。若全部限制为 `0`，MyBot 不预先拦截图片。模型服务最终失败时向群成员说明本轮已结束；反馈和日志仅保留模型错误分类及 HTTP 状态，省略可能含密钥的供应商响应正文。

`retain_images = false` 时 direct 图片只服务当前轮；设为 `true` 后原始字节会保留到当前进程的长期上下文，并受上下文压缩及总字节设置约束。进程重启后内存上下文仍会丢失，NapCat 断线重连不会清空当前进程内的上下文。同群请求分别读取长期上下文快照并发执行，待模型回复完成后按完成顺序提交；运行中的请求不会把未完成内容暴露给其他请求。

`tool_result_retention` 支持 `off`、`summary` 和 `full`：分别不保存、只保存工具名称及成功/失败次数、保存工具调用参数和完整结果。`retain_reasoning` 只控制 reasoning 是否跨用户轮次保存。`debug_dump_messages` 和 `debug_dump_directory` 明确控制长期上下文调试文件。消息格式化、历史分页、群文件列表和 token 估算系数也全部位于 `[plugins.ai_group_chat.*]` 配置节；格式化字符/条目上限和工具单次上限为 `0` 时不限，格式化深度为 `-1` 时不限。

主模型的每次正式回复与历史压缩请求都检查完整上下文预算。超限历史会按预算分段压缩，合并后的摘要仍过长时继续压缩摘要，直到能与当前问题一起放入正式请求；空摘要或无法继续缩短的摘要会明确反馈失败。工具结果超出本轮预算时，较大的结果会替换成 `ToolResultTooLarge` 错误，供模型减小查询范围后继续；保存的工具记录与实际交付内容一致。仍无法容纳的请求会结束本轮并反馈。历史工具的 `max_per_call` 同时限制普通分页与前后文查询；前后文包含锚点，两侧均分其余名额，未用名额交给另一侧。

### Neavo 群聊图像插件

群成员使用配置的 `generate_command` 触发文生图，使用 `describe_command` 加图片或回复含图消息进行图片反推。输入和输出图片分别由 `max_input_image_bytes`、`max_output_image_bytes` 控制，`0` 表示不限；反推格式列表由 `allowed_input_mime_types` 控制，空列表表示不设 MIME 白名单。

```toml
[plugins.neavo_image_generate]
groups = ["123456789"]
base_url = "https://image-api.example.com"
api_token = "CHANGE_ME"
poll_interval_seconds = 3
generation_timeout_seconds = 600
request_timeout_seconds = 30
max_prompt_chars = 4096
max_input_image_bytes = 10485760
max_output_image_bytes = 20971520
allowed_input_mime_types = ["image/jpeg", "image/png", "image/webp"]
max_consecutive_poll_errors = 3
generate_command = "#生图"
describe_command = "#反推"
```

省略整个插件节即可停用；Neavo 服务不要求鉴权时可以省略 `api_token`。实际 Token 不得提交或写入日志。

## 运行边界

- `app/api/`：NapCat Action 封装。
- `app/models/`：NapCat 协议模型和 JSON 边界类型。
- `app/services/napcat/`：可复用的 NapCat 本地工具和图片读取服务。
- `app/services/llm/`：模型路由、OpenAI 协议、MCP 和工具注册。
- `app/plugins/`：插件业务编排。
- `app/database/`：PostgreSQL、migration、群消息 repository 和图片任务；不保存图片字节。
- `app/config/`：唯一配置模型、加载器、配置版本和目录监听。
- `app/webui/`：配置控制台 API、保留 TOML 注释的写回和 SPA 静态文件挂载。

插件必须声明稳定的 ASCII `plugin_id`。每个插件只获得绑定自身 ID 的类型化配置视图，不能通过公共接口读取启动配置或其他插件配置。插件私有关系数据使用 `plugin_<plugin_id>` schema、自有 migration 和类型化 repository；插件不直接持有 `AsyncSession`，也不通过通用 JSONB KV 保存状态。插件之间不导入、调用或订阅彼此，需要共用的能力放入公共模块。

## 失败策略

配置缺失、协议不一致、PostgreSQL 不可用或 migration 版本不匹配会直接失败。群消息持久化按 `database.persistence_retry_delays_seconds` 明确给出的等待序列重试；全部尝试失败后不再分发该事件，并以 1011 关闭当前 NapCat 会话。工具参数、回复标记或图片读取等可恢复错误会返回结构化信息，让模型或插件继续处理。

## 开发检查

```bash
uv lock --check
docker compose config --quiet
uv run pytest
uv run basedpyright
uv run python -m compileall app
cd webui && npm run build
git diff --check
```

`basedpyright` 必须保持 `0 errors, 0 warnings`。详细流程见 [运行架构](docs/runtime_architecture.md)。
