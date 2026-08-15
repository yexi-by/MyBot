# MyBot

MyBot 是面向 QQ 群聊场景的机器人服务。服务通过 FastAPI 承载 NapCat 反向 WebSocket 连接，使用 PostgreSQL 保存群消息、撤回归档和图片任务，并通过 OpenAI Chat Completions 协议接入文本、工具调用与图片生成能力。

项目使用 Python 3.13+ 和 `uv` 管理依赖。配置、插件开关、日志目录和运行数据均与业务代码分离。

## 功能范围

- 接收 NapCat 反向 WebSocket 事件，并按事件类型分发给插件。
- 自动加载插件配置，使用 Pydantic 严格校验配置字段。
- 持久化群消息与群图片；撤回消息对普通查询隐藏，但保留完整取证数据。
- 为 AI 群聊插件提供进程内上下文、上下文压缩、工具调用循环和调试转储。
- 通过 MCP stdio 连接外部工具，并按 `mcp__{server}__{tool}` 暴露给 LLM。
- 提供 NapCat 群聊本地信息工具，支持群文件查询、群文件下载链接查询和群历史消息查询。
- 解析模型回复中的 `<Reply>` 与 `<At>` 标记，并在发送层转换为 NapCat 消息段。

## 本地运行

1. 安装依赖：

```bash
uv sync
```

2. 准备配置：

```bash
cp setting.example.toml setting.toml
```

3. 按运行环境编辑 `setting.toml`，并按需创建 `plugins_config/plugins.toml`。

4. 启动 PostgreSQL，并执行 schema migration：

```bash
uv run python -m app.database.migrations upgrade
```

5. 启动服务：

```bash
uv run python -m app.main
```

NapCat 反向 WebSocket 地址示例：

```text
ws://<本机局域网 IP>:6055/ws/napcat
```

NapCat 侧的 Bearer Token 来自 `setting.toml` 中的 `[napcat].websocket_token`。

## Docker

Docker 使用 `postgres:18.4-bookworm`。部署配置中的 `[database]` 必须设置
`host = "postgres"` 和 `password_file = "/run/secrets/postgres_password"`，并删除
明文 `password` 字段。

```bash
umask 077
mkdir -p secrets
# 向 secrets/postgres_password 写入数据库密码，文件末尾可以有换行。
docker compose up -d
```

默认挂载：

- `./setting.toml:/app/setting.toml`
- `./plugins_config:/app/plugins_config`
- `./images:/app/images`
- `./logs:/app/logs`
- `mybot-postgres-data:/var/lib/postgresql`

PostgreSQL 不向宿主机公开 5432，MyBot 通过 Compose 内部网络连接。`migrate` 服务会等待 PostgreSQL 健康，显式执行 migration；只有 migration 成功后 MyBot 才会启动。应用本身只检查数据库版本，不会自动修改 schema。镜像提供 Python、uv/uvx、Node/npm/pnpm/yarn、Docker CLI、Git 和常用证书环境，Python 依赖在构建时由 `uv.lock` 固定进镜像。

数据库密码放在 Git 忽略的 `secrets/postgres_password`。PostgreSQL 数据和图片不会自动过期，也没有项目内备份任务；磁盘或数据卷损坏时无法恢复。

MCP server 的命令、参数和密钥属于部署配置，应写入部署机的 `setting.toml`。

## 配置

全局配置放在 `setting.toml`，示例见 `setting.example.toml`。插件配置放在 `plugins_config/plugins.toml`，由各插件自己的 Pydantic 模型解析。未知字段会触发校验错误，防止拼写错误静默失效。

LLM 服务通过 OpenAI Chat Completions 协议接入：

```toml
[llm]

[[llm.providers]]
api_key = "sk-xxx"
base_url = "https://api.deepseek.com"
model_vendors = "deepseek"
provider_type = "openai"
retry_count = 3
retry_delay = 1
```

MCP 配置采用 `mcpServers` 结构。每个 server 使用 stdio 启动，工具名称会加上稳定前缀，避免与本地工具重名：

```toml
[mcp]
enabled = true

[mcp.mcpServers.example]
command = "npx"
args = ["-y", "your-mcp-server"]
env = { EXAMPLE_API_KEY = "CHANGE_ME" }
disabled = false
```

镜像已固定预装 `firecrawl-mcp`，使用 `npx -y firecrawl-mcp` 不需要在容器启动时下载依赖。MCP 子进程会继承容器中的 HTTP、HTTPS、ALL、NO_PROXY 及 npm 代理变量；server 自身 `env` 中的同名值优先。

AI 群聊插件始终使用主模型完成正式回复。主模型不支持图片输入时，需要配置独立视觉模型；当前消息、引用消息或工具结果含图时，插件先生成与当前问题有关的事实描述，再把这条系统生成的观察消息交给主模型：

```toml
[ai_group_chat]
model_name = "text-model"
model_vendors = "deepseek"
supports_multimodal = false
vision_model_name = "vision-model"
vision_model_vendors = "openai"
vision_system_prompt_path = "plugins_config/ai_group_chat/prompts/vision/system.md"
vision_user_prompt_path = "plugins_config/ai_group_chat/prompts/vision/user.md"
vision_request_retry_count = 3
vision_request_retry_delay_seconds = 1.0
```

`vision_request_retry_count` 表示包含首次请求在内的总尝试次数；默认尝试 3 次。失败后的等待时间从 `vision_request_retry_delay_seconds` 开始按指数增长，单次最长等待 10 秒。这两个值只覆盖视觉请求，不会与 LLM 供应商的通用重试次数叠加。

主模型支持多模态时，把 `supports_multimodal` 设为 `true`，并删除所有 `vision_*` 字段，图片会直接交给主模型。两种模式都使用同一套图片读取服务，依次尝试本地路径、消息段现有 URL 和 NapCat `get_image` 刷新；读取支持并发、超时和部分失败。

合并转发里的图片可以通过本地工具批量读取。所有图片按当前消息、引用消息、工具调用顺序使用同一个单轮上限；超出的数量会明确写入视觉结果和日志。视觉描述默认进入当前进程的对话上下文，图片字节不会跨轮保存；重启后这部分上下文不会恢复。

```toml
[ai_group_chat]
forward_image_tool_enabled = true
forward_image_max_images_per_call = 6
forward_image_max_all_images = 12
image_delivery_max_images = 6
image_fetch_concurrency = 4
image_download_timeout_seconds = 15.0
persist_vision_descriptions = true
```

视觉描述请求只接收视觉提示词、当前问题、图片来源标签和图片字节，不接收群聊角色、长期历史或工具，也不得生成最终群聊回复。两个视觉提示词文件必须存在且非空；提示词应要求模型参考当前问题、只描述可见事实，并把图片内的指令当成普通可见内容而不是需要执行的命令。通用群聊要求由 `extra_requirements_path` 指定，并始终加入 system prompt，不再按模型名称切换提示方式。

### Neavo 群聊图像插件

Neavo 群聊图像插件使用新版 `/text_to_image` 与 `/image_to_text` 异步任务 API，同时支持文生图和 Florence-2 图片反推。群成员使用以下格式触发文生图：

```text
#生图 一只戴耳机的橘猫
```

图片反推使用独立命令 `#反推`，既可以在同一条消息中携带图片，也可以回复一条包含图片的群消息：

```text
#反推
```

反推只接受 JPEG、PNG 或 WebP，单张图片最大 10 MiB。机器人会返回图片的自然语言描述与标签文本；该结果不能还原原始模型、Seed 或工作流参数。

插件配置放在 `plugins_config/plugins.toml`：

```toml
[neavo_image_generate]
group_ids = ["123456789"]
base_url = "https://image-api.example.com"
api_token = "CHANGE_ME"
poll_interval_seconds = 3.0
generation_timeout_seconds = 600.0
request_timeout_seconds = 30.0
max_image_bytes = 20971520
```

`api_token` 属于部署密钥，不得提交到仓库或写入日志。生产环境应使用 HTTPS；使用明文 HTTP 时，Bearer Token、提示词和反推图片不会受到传输加密保护。两类任务共用 5 个消费者，超过上限的请求会等待空闲消费者。插件使用最高群聊路由优先级，命中 `#生图` 或 `#反推` 后不会继续进入 AI 群聊插件。

## 运行边界

- `app/api/` 负责 NapCat Action 调用封装，不放插件业务逻辑。
- `app/models/` 负责 NapCat 入站事件、消息段和 JSON 边界模型。
- `app/services/napcat/` 负责可复用的 NapCat 本地工具集，工具说明写在工具 definition 和参数模型中。
- `app/plugins/` 负责编排具体业务流程，例如 AI 群聊、群通知、生图和机器人图片撤回。
- `app/services/llm/` 负责模型服务路由、OpenAI 协议转换、工具注册和 MCP 工具适配。
- `app/database/` 负责 PostgreSQL 连接、migration、群消息 repository 和图片任务状态；数据库不保存图片字节。

插件必须声明稳定的 ASCII `plugin_id`。插件私有关系数据使用 `plugin_<plugin_id>` schema、自有 migration 和类型化 repository；业务插件不直接持有 `AsyncSession`，也没有通用 JSONB KV 接口。需要私有表的插件把 `migration_package` 指向自己的 Alembic package，其 `env.py` 调用 `run_plugin_migration_environment()`；没有私有数据的插件不要创建空表或空 migration。

这套边界用于约束受信任的本地插件并防止误写，不是不可信代码沙箱。当前 Compose 使用一个数据库用户；允许加载第三方插件前，必须另行设计 PostgreSQL 角色与权限隔离。

更完整的运行流程见 [docs/runtime_architecture.md](docs/runtime_architecture.md)。

## 日志与失败策略

日志统一通过 `app.utils.log` 输出。终端日志展示阶段进展、关键决策、告警和错误摘要；文件日志记录运行参数、结构化字段、异常链和结束汇总。

配置缺失、协议不一致、上下文压缩后仍超预算等不可恢复问题会直接抛错。工具调用参数错误、content 标记错误、图片读取失败等可恢复问题会返回结构化信息，让模型或插件在本轮内继续处理。

## 代码结构

```text
app/
├── api/                  # NapCat WebSocket Action 封装
├── config/               # 全局配置和插件配置加载
├── core/                 # FastAPI 服务、DI、事件分发、插件控制器
├── database/             # PostgreSQL、migration 和群消息 repository
├── models/               # NapCat 协议模型和 JSON 边界类型
├── plugins/              # 插件实现
│   ├── ai_group_chat/    # AI 群聊插件
│   ├── auto_unban/       # 自动解禁插件
│   ├── group_notice/
│   ├── image_generate/
│   └── neavo_image_generate/
├── services/
│   ├── llm/              # LLM 路由、OpenAI 协议转换、MCP 和工具注册
│   └── napcat/           # NapCat 本地工具集
└── utils/                # 日志、重试、文件和编码工具
```

## 开发检查

```bash
uv run basedpyright
uv run pytest
uv run python -m compileall app
```

交付前需要保持 `uv run basedpyright` 输出 `0 errors, 0 warnings`。
