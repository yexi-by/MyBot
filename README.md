# MyBot

MyBot 是面向 QQ 群聊的机器人服务。它通过 FastAPI 接收 NapCat 反向 WebSocket 事件，使用 PostgreSQL 保存群消息、撤回记录和群媒体，并通过 Chat Completions 或 Responses API 连接语言模型。

项目要求 Python 3.13+，依赖由 `uv` 管理。

## 主要功能

- 按事件类型把 NapCat 消息交给内置插件。
- 保存入站和出站群消息；撤回后普通查询不可见，但原文和图片仍保留。
- 提供 AI 群聊、原图输入、上下文压缩、MCP 和 NapCat 群聊工具。
- 用唯一配置文件管理服务、模型和插件；插件配置和引用的文本文件支持自动热加载。
- 提供同端口 WebUI，用表单编辑配置和 prompt，并自动校验、保存改动。
- 解析模型回复中的 `<Reply>` 与 `<At>` 标记，并转换为 NapCat 消息段。

## 本地运行

```bash
uv sync
mkdir config
cp config.example.toml config/mybot.toml
cp -r config.example/. config/
```

编辑 `config/mybot.toml`；其中引用的提示词已随 `config.example/` 复制到 `config/`，默认人格见「默认提示词与人格」。随后启动 PostgreSQL、执行 migration 并运行服务：

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

开发页面位于 `http://127.0.0.1:5173/`，Vite 会把 `/api` 转发到 6056。开发后端不依赖 PostgreSQL，可以用 `--config` 指向一份临时配置联调。

控制台按 `mybot.toml` 的结构分页：系统设置、模型服务、MCP 服务、每个插件各一页，以及编辑 `config/` 文本的「配置文本」页。每页都有独立地址（如 `/plugins/ai_group_chat`、`/files?path=mybot.toml`），可以直接收藏或刷新。

配置字段停止编辑 800ms 后自动校验并写回，文本文件停止编辑 1 秒后自动保存；写回只改动变化的键，保留 TOML 注释与格式。非法值、空的必填 prompt 和内容哈希冲突都不会覆盖磁盘文件，错误标在对应字段上。顶栏状态显示待写入、写入中、待重启或插件热生效的进度，右侧「写回记录」逐条列出本次会话写入的改动。文件页允许读写 `config/` 内任意扩展名的 UTF-8 文本，包括 `mybot.toml`；TOML 语法损坏时控制台会直接打开原文供修复。

`Ctrl+S` 立即保存当前配置或文本文件，`Ctrl+K` 打开命令面板搜索字段、页面和文件，`/` 聚焦本页字段筛选。删除 Provider、MCP 服务、群或停用插件前会二次确认。磁盘文件被外部修改时，可以选择覆盖外部修改、丢弃并刷新或继续编辑；文本文件还可以先复制本地内容。模型名输入框会从所选 Provider 拉取模型列表供筛选，拉取失败时仍可手输。切换页面会保留已打开的文本编辑会话并继续自动保存；重启和关机会先写完待保存内容，关闭浏览器页面时如有未保存内容也会提示。

## Docker

Compose 使用 `postgres:18.4-bookworm`，PostgreSQL 不向宿主机公开端口。容器内配置必须使用 `database.host = "postgres"` 和 `database.password_file = "/run/secrets/postgres_password"`。

```bash
umask 077
mkdir -p config secrets images logs
cp config.example.toml config/mybot.toml
cp -r config.example/. config/
# 编辑 config/mybot.toml，并向 secrets/postgres_password 写入数据库密码。
docker compose up -d
```

主要挂载如下：

- `./config:/app/config:rw`（MyBot 在线保存配置）
- `./images:/app/images`
- `./logs:/app/logs`
- `mybot-postgres-data:/var/lib/postgresql`

`migrate` 会等待 PostgreSQL 健康后执行 migration，成功后 MyBot 才启动。应用启动时只检查 migration 版本，不会自动修改 schema。数据库和媒体文件没有自动过期或备份机制。

图片和视频共用 `storage.images` 配置及持久化目录，按 SHA-256 去重保存原文件；数据库只记录任务、文件类型、大小和存储键。引用消息时，仓库会将已归档的本地路径补回对应消息段，优先读取归档文件。归档独立于 AI 视频输入开关，关闭输入后收到的视频仍会保存，供日后引用。

媒体归档先读取消息段已有的本地文件，再尝试已有 URL，失败后向 NapCat 刷新来源：图片用 `get_image`，视频用 `get_file`。`storage.images.download_timeout_seconds` 分别限制首次下载、刷新信息和刷新后下载；完整读取预算为该值的三倍，任务租约在此基础上加上配置的 `lease_seconds`，为存储和状态写回保留时间。重试间隔由 `retry_delays_seconds` 决定，用尽次数后任务标记为失败。

NapCat 返回的本地缓存路径属于 NapCat 所在的文件系统。分容器部署需要把对应缓存目录以相同容器路径只读挂载到 MyBot，才能使用本地缓存兜底；没有共享缓存时会继续尝试刷新后的 URL。

默认 Compose 允许 MyBot 通过 WebUI 在线保存 `config/`，`migrate` 服务仍保持只读挂载。WebUI 面向可信内网使用，不应直接暴露到公网。

## 配置

唯一配置文件是 `config/mybot.toml`，完整字段见 [config.example.toml](config.example.toml)。所有配置模型都禁止未知字段。

运行参数必须在配置中明确填写。项目不会在运行时用常量补齐缺失的超时、重试、并发、数量或大小限制；字段缺失会在启动或 WebUI 校验时返回完整字段路径。可关闭的限制使用文档中声明的 `0` 或空列表语义，不依赖省略字段。

插件消费者数量和优先级使用 `[plugin_execution.plugins.<plugin_id>]` 动态映射。启动时会比较已加载插件与配置键；缺少执行配置或配置了不存在的插件都会直接报出对应 ID。

`plugin_execution.blocked_user_ids` 配置不响应的用户 QQ 列表，也可在 WebUI「系统设置 → 插件执行 → 屏蔽用户 QQ」中编辑，重启后生效。这些用户的群聊和私聊消息会在进入插件链前被跳过；群消息持久化和撤回、禁言等通知事件照常处理。

### 热加载范围

应用自动监听 `config/`，连续文件变化会合并处理：

- `[plugins.*]`、这些配置引用的 prompt、知识库和通用要求文件会热加载。
- 插件配置节存在即启用；删除该节即停用。当前事件继续使用取得时的旧配置，下一条相关事件使用新配置。
- TOML 不完整、字段无效或引用文件不可读时，整次重载失败，现有配置继续生效。
- `[app]`、`[server]`、`[napcat]`、`[storage]`、`[network]`、`[logging]`、`[llm]`、`[mcp]`、`[database]` 和 `[plugin_execution]` 只在启动时生效。运行中修改这些节会记录需要重启，但同一次保存中的有效插件变化仍会应用。
- 新增 LLM provider 必须重启。热加载期间，插件只能引用进程启动时已经注册的 provider。

配置引用文件必须使用相对于 `config/` 的路径。绝对路径、越出目录的 `..` 和指向目录外的符号链接都会被拒绝。人格提示词和通用要求文件不能为空；知识库可以省略或留空。

### LLM provider 与模型引用

Provider ID 直接使用表名：

```toml
[llm.providers.main]
api_format = "chat_completions"
api_key = "sk-CHANGE_ME"
base_url = "https://api.openai.com/v1"
inherit_network_proxy = true
timeout_seconds = 0
max_attempts = 5
retry_delay_seconds = 0
retry_max_delay_seconds = 10
```

`api_format` 必须明确指定为 `chat_completions` 或 `responses`，分别使用 Chat Completions 与 Responses API，不会自动切换协议。WebUI「模型服务」中提供对应选项。无鉴权服务可以省略 `api_key`，此时不会发送 `Authorization` 请求头。`timeout_seconds = 0` 表示使用 `[network].timeout_seconds`；`retry_max_delay_seconds = 0` 表示指数退避不设最大等待时间。Provider 可单独配置 `proxy`，也可通过 `inherit_network_proxy` 决定是否继承全局代理。

插件使用 `{ provider, name }` 引用模型，例如：

```toml
model = { provider = "main", name = "your-multimodal-model" }
```

### MCP

MCP server 支持 stdio 和 Streamable HTTP，工具名会转换为 `mcp__{server}__{tool}`：

```toml
[mcp]
enabled = true
initialization_timeout_seconds = 30
call_timeout_seconds = 60

[mcp.servers.github]
transport = "stdio"
command = "github-mcp-server"
args = ["stdio"]
env = { GITHUB_PERSONAL_ACCESS_TOKEN = "CHANGE_ME" }
disabled = false

[mcp.servers.firecrawl]
transport = "streamable_http"
url = "https://mcp.firecrawl.dev/v2/mcp"
headers = { Authorization = "Bearer fc-CHANGE_ME" }
disabled = false
```

MCP 命令、环境变量和认证头属于部署配置，不应提交真实密钥。stdio 服务配置 `command`、`args` 以及可选的 `env`、`cwd`；Streamable HTTP 服务配置 `url` 和可选的 `headers`，不能混用两种传输的字段。省略 `transport` 时使用 stdio。
`initialization_timeout_seconds` 限制每个服务握手与工具清单加载的总等待时间；初始化失败会释放已启动的 MCP 资源。`call_timeout_seconds` 限制单次工具调用，超时会把结构化错误返回给 AI，后续调用仍可继续。两个字段均须明确填写正数。
Firecrawl 通过 Streamable HTTP 连接[官方托管 MCP API](https://docs.firecrawl.dev/mcp-server)，认证头使用 `Authorization: Bearer <API Key>`。
镜像内包含 `/usr/local/bin/github-mcp-server`，可直接使用上述 stdio 配置。运行镜像不附带 Node.js 或 npm；其他 stdio 服务的可执行文件需由部署环境提供，也可以直接连接 Streamable HTTP 服务。

### AI 群聊与图片输入

AI 群聊由主模型处理文字和图片并生成正式回复。图片以原始字节直接进入所选协议的多模态请求，不缩放或重新编码；服务端和模型需支持图片输入。Chat Completions 使用 `image_url`，Responses API 使用 `input_image`。

```toml
[plugins.ai_group_chat]
model = { provider = "main", name = "your-multimodal-model" }
extra_requirements_file = "ai_group_chat/prompts/extra_requirements.md"
tool_result_retention = "off"

[plugins.ai_group_chat.images]
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
system_prompt_file = "ai_group_chat/prompts/roles/maodie.md"
knowledge_base_file = "ai_group_chat/knowledge/default.md"
max_context_tokens = 64000
```

图片读取依次尝试已有路径、现有 URL 和 NapCat `get_image` 刷新。图片数量、单图原始字节、单次请求图片总原始字节、宽高、MIME 类型、下载并发、超时及转发图片额度都由配置决定；数量、字节和宽高字段为 `0` 时不限，MIME 列表为空时不限制格式。超过正数限制时按 `oversize_behavior` 执行：`error` 立即结束本轮并报错，`skip` 跳过单图并把原因告诉主模型。若全部限制为 `0`，MyBot 不预先拦截图片。模型服务最终失败时向群成员说明本轮已结束；反馈和日志仅保留模型错误分类及 HTTP 状态，省略可能含密钥的供应商响应正文。

`retain_images = false` 时图片只服务当前轮；设为 `true` 后原始字节会保留到当前进程的长期上下文，并受上下文压缩及总字节设置约束。进程重启后内存上下文仍会丢失，NapCat 断线重连不会清空当前进程内的上下文。同群请求分别读取长期上下文快照并发执行，待模型回复完成后按完成顺序提交；运行中的请求不会把未完成内容暴露给其他请求。

模型响应在协议入口和群消息发送前接受完整性检查：截断或异常结束、缺失或无效的结构化工具调用，以及正文中的 `<call:…>`、`<think>`、`<thought>`、`🫧thought`、`💭thinking` 和 `<|channel|>` 等明确内部标记都会使整份响应被拒绝。单独一行的 `💭` 或 `🫧` 后面跟着正文，也按异常思考标题处理。检查依据输出格式，不按模型名称分支，也不把普通英文或 thought、think 等单词当作异常。句中表情、单个表情消息和单个行内标记引用可正常展示，独占一行的思考标题不会因加了反引号而放行。混合正文不猜测切分点、不只删除标记。

结构或内容异常会直接结束本轮，不追加纠错提示。被拒绝的正文和调用不会发群、执行或写入长期上下文，也不会作为错误原文回传模型。只有网络连接失败、超时、限流和 HTTP 5xx 按 `max_attempts` 与退避配置重试，重试输入保持不变；耗尽后明确结束本轮。历史摘要使用相同的协议入口检查。

Chat Completions 从 `message.content` 读取正式回复，缺少正文时读取 `message.refusal`，不猜测响应中附加的 reasoning 字段；工具调用的 `extra_content` 原样传递以保留签名。Responses 的原始输出项只用于当前轮工具续问，不作为群回复或长期上下文保存。无效的 `<Reply>`、`<At>` 标记仍作为可恢复发送错误处理：把发送失败事实返回模型，不附加重写指令。

已确认异常的历史助手正文会从本轮模型请求、token 预算和压缩摘要输入中隔离，已执行的工具调用与结果保持配对。最近群聊、引用消息和历史查询同样隔离机器人自己的异常旧回复，历史工具以 `content_quarantined = true` 标注；群成员消息、正常机器人回复和数据库原始记录保持原样。隔离不会清空整个群的历史，也不会新增业务配置项。完全无标记、与正常解释难以区分的内容不做关键词强拦截。

`tool_result_retention` 支持 `off`、`summary` 和 `full`：分别不保存、只保存工具名称及成功/失败次数、保存工具调用参数和完整结果。消息格式化、历史分页、群文件列表和 token 估算系数也全部位于 `[plugins.ai_group_chat.*]` 配置节；格式化字符/条目上限和工具单次上限为 `0` 时不限，格式化深度为 `-1` 时不限。

`max_context_tokens` 是长期上下文的压缩预算。每轮开始时检查已保存的系统提示词与历史消息，超限后把历史压缩为独立摘要，再接上本轮输入；系统提示词与摘要合计需回到预算内。当前问题、最近群聊记录、工具定义、轮内工具调用和临时图片可超出此预算，工具结果完整交给模型。按配置保存的本轮内容可以使长期历史暂时超限，由下一轮开始时统一压缩。历史压缩请求本身也属于临时上下文，可携带超过长期预算的历史；摘要仍过长时继续压缩，空摘要或无法继续缩短的摘要会明确反馈失败并保留原历史，后续可重试。压缩通知发送失败会记录日志并继续整理。模型服务自身的上下文窗口限制仍由 Provider 执行。

历史工具的 `max_per_call` 同时限制普通分页与前后文查询；前后文包含锚点，两侧均分其余名额，未用名额交给另一侧。

### AI 群聊语境

AI 群聊仍只在被 @ 时回复。当前消息按聊天记录交给模型：发送时间、发送者昵称和 QQ（群主、管理员附带身份）、消息 ID、引用内容与正文；指向机器人自身的 @ 会被去掉，只 @ 不说话时会明确标出。

配置 `[plugins.ai_group_chat.recent_chat]` 后，每次被 @ 时从消息仓库读取该消息之前最多 `max_messages` 条群消息，整理成带时间和发送者的简短记录随本轮请求发送，机器人自己的发言标为「你」。这份记录只服务当前轮，不写入长期上下文；只读取本进程启动之后的消息，因此重启后语境从空白开始。`max_chars_per_message` 限制单条消息在记录中的字符数，`0` 表示不截断。删除整节即关闭。

```toml
[plugins.ai_group_chat.recent_chat]
max_messages = 30
max_chars_per_message = 200
```

### 默认提示词与人格

仓库的 `config.example/` 与 `config/` 目录结构一致，提供 `config.example.toml` 引用的全部提示词，默认人格是性格温柔的猫娘「耄耋」：

- `ai_group_chat/prompts/roles/maodie.md`：群聊角色提示词，写耄耋的性格、说话方式、联网查资料的习惯和分寸。
- `ai_group_chat/knowledge/default.md`：群知识库模板，可写群友称呼、群梗和群规；删空后不会附加到提示词。
- `ai_group_chat/prompts/extra_requirements.md`：所有群共用的通用要求，说明 `<Reply>`、`<At>` 标记和纯文本输出。

每个群的固定 system prompt 按「角色提示词 → 知识库 → 通用要求」拼接，其中任一文件变化都会清空该群的内存上下文。这些文件只是新部署的起点，复制到 `config/` 后可在 WebUI 文件页直接修改；已部署环境以自身的 `config/` 为准，仓库示例不会覆盖它。

### 群聊视频输入

在 AI 群聊页面开启「接受视频输入」，或配置：

```toml
[plugins.ai_group_chat.videos]
enabled = true
max_per_turn = 2
fetch_concurrency = 2
download_timeout_seconds = 60
max_video_bytes = 20971520
```

开启后，当前消息、引用消息及已内嵌转发中的视频会按来源顺序交给主模型，优先处理当前消息。主模型及服务端需支持 Chat Completions 的 `video_url` 扩展，完整原文件以 `data:video/...;base64,...` 传入。Responses API 不支持视频输入；收到视频时明确返回不支持错误，不自动转为图片或忽略视频。仅启用视频但本轮没有视频时，Responses 的文字和图片请求仍可正常执行。关闭输入时只保留视频消息元信息，不读取视频作为模型输入。

`max_per_turn` 和 `max_video_bytes` 为 `0` 时不限。超限、下载失败或超时会返回明确的部分错误，其余视频与文字继续处理。归档文件受系统「媒体存储」的 `storage.images.max_bytes` 限制；模型输入受这里的 `max_video_bytes` 限制。原文件持久归档；模型请求里的视频字节只保留到本轮结束，后续轮次可以通过引用原消息重新读取。示例配置默认关闭视频输入，以便按主模型能力选择开启。

## 运行边界

- `app/api/`：NapCat Action 封装。
- `app/models/`：NapCat 协议模型和 JSON 边界类型。
- `app/services/napcat/`：可复用的 NapCat 本地工具和媒体读取服务。
- `app/services/llm/`：模型路由、OpenAI 协议、MCP 和工具注册。
- `app/plugins/`：插件业务编排。
- `app/database/`：PostgreSQL、migration、群消息 repository 和媒体任务；不保存媒体字节。
- `app/config/`：唯一配置模型、加载器、配置版本和目录监听。
- `app/webui/`：配置控制台 API、保留 TOML 注释的写回和 SPA 静态文件挂载。
- `webui/`：配置控制台前端（React + TypeScript + Vite）；表单由 `webui/src/schema/` 的字段定义生成。

插件必须声明稳定的 ASCII `plugin_id`。每个插件只获得绑定自身 ID 的类型化配置视图，不能通过公共接口读取启动配置或其他插件配置。群消息通过核心仓库的窄接口访问，插件不直接持有 `AsyncSession`。插件之间不导入、调用或订阅彼此，需要共用的能力放入公共模块。

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
