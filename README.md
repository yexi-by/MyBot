# MyBot

MyBot 是面向 QQ 群聊的机器人服务。它通过 FastAPI 接收 NapCat 反向 WebSocket 事件，使用 PostgreSQL 保存群消息、撤回记录和群图片，并通过 OpenAI Chat Completions 协议连接语言模型。

项目要求 Python 3.13+，依赖由 `uv` 管理。

## 主要功能

- 按事件类型把 NapCat 消息交给内置插件。
- 保存入站和出站群消息；撤回后普通查询不可见，但原文和图片仍保留。
- 提供 AI 群聊、上下文压缩、视觉描述、MCP 和 NapCat 群聊工具。
- 用唯一配置文件管理服务、模型和插件；插件配置和引用的文本文件支持自动热加载。
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

Bearer Token 来自 `[napcat].websocket_token`。

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

- `./config:/app/config:ro`
- `./images:/app/images`
- `./logs:/app/logs`
- `mybot-postgres-data:/var/lib/postgresql`

`migrate` 会等待 PostgreSQL 健康后执行 migration，成功后 MyBot 才启动。应用启动时只检查 migration 版本，不会自动修改 schema。数据库和图片没有自动过期或备份机制。

## 配置

唯一配置文件是 `config/mybot.toml`，完整字段见 [config.example.toml](config.example.toml)。所有配置模型都禁止未知字段。

### 热加载范围

应用自动监听 `config/`，连续文件变化会合并处理：

- `[plugins.*]`、这些配置引用的 prompt、知识库和通用要求文件会热加载。
- 插件配置节存在即启用；删除该节即停用。当前事件继续使用取得时的旧配置，下一条相关事件使用新配置。
- TOML 不完整、字段无效或引用文件不可读时，整次重载失败，现有配置继续生效。
- `[app]`、`[server]`、`[napcat]`、`[storage]`、`[network]`、`[logging]`、`[llm]`、`[mcp]` 和 `[database]` 只在启动时生效。运行中修改这些节会记录需要重启，但同一次保存中的有效插件变化仍会应用。
- 新增 LLM provider 必须重启。热加载期间，插件只能引用进程启动时已经注册的 provider。

配置引用文件必须使用相对于 `config/` 的路径。绝对路径、越出目录的 `..` 和指向目录外的符号链接都会被拒绝。system、vision 和通用要求文件不能为空；知识库可以省略或留空。

### LLM provider 与模型引用

Provider ID 直接使用表名：

```toml
[llm.providers.deepseek]
api_key = "sk-CHANGE_ME"
base_url = "https://api.deepseek.com"
max_attempts = 5
retry_delay_seconds = 0
```

插件使用 `{ provider, name }` 引用模型，例如：

```toml
model = { provider = "deepseek", name = "deepseek-chat" }
```

### MCP

MCP server 使用 stdio 启动，工具名会转换为 `mcp__{server}__{tool}`：

```toml
[mcp]
enabled = true

[mcp.servers.example]
command = "npx"
args = ["-y", "your-mcp-server"]
env = { EXAMPLE_API_KEY = "CHANGE_ME" }
disabled = false
```

MCP 命令、环境变量和密钥属于部署配置，不应提交到仓库。

### AI 群聊与视觉描述

AI 群聊始终由主模型生成正式回复。主模型支持图片时，图片直接交给主模型，并且不得配置 `[plugins.ai_group_chat.vision]`。主模型不支持图片时必须配置 vision；视觉模型只接收当前问题、图片和视觉提示词，不接收角色、历史或工具。

```toml
[plugins.ai_group_chat]
model = { provider = "deepseek", name = "deepseek-chat", supports_images = false }
extra_requirements_file = "ai_group_chat/prompts/extra_requirements.md"
show_reasoning = false
retain_reasoning = false

[plugins.ai_group_chat.vision]
model = { provider = "vision", name = "vision-model" }
system_prompt_file = "ai_group_chat/prompts/vision/system.md"
user_prompt_file = "ai_group_chat/prompts/vision/user.md"
max_attempts = 5
retry_delay_seconds = 0.25
retain_descriptions = true

[[plugins.ai_group_chat.groups]]
id = "123456789"
system_prompt_file = "ai_group_chat/prompts/roles/default.md"
knowledge_base_file = "ai_group_chat/knowledge/default.md"
max_context_tokens = 64000
```

图片读取依次尝试已有路径、现有 URL 和 NapCat `get_image` 刷新。视觉描述可以保留在当前进程的对话上下文，图片字节不会进入上下文；进程重启后上下文仍会丢失。同群请求串行执行，不同群可以并行。prompt、知识库或通用要求内容变化后，只清空受影响群的内存上下文。

### Neavo 群聊图像插件

群成员使用 `#生图 提示词` 触发文生图，使用 `#反推` 加图片或回复含图消息进行图片反推。反推接受 JPEG、PNG 或 WebP，单图最大值由 `max_image_bytes` 控制。

```toml
[plugins.neavo_image_generate]
groups = ["123456789"]
base_url = "https://image-api.example.com"
api_token = "CHANGE_ME"
poll_interval_seconds = 3
generation_timeout_seconds = 600
request_timeout_seconds = 30
max_image_bytes = 20971520
```

省略整个插件节即可停用。`api_token` 不得提交或写入日志。

## 运行边界

- `app/api/`：NapCat Action 封装。
- `app/models/`：NapCat 协议模型和 JSON 边界类型。
- `app/services/napcat/`：可复用的 NapCat 本地工具和图片读取服务。
- `app/services/llm/`：模型路由、OpenAI 协议、MCP 和工具注册。
- `app/plugins/`：插件业务编排。
- `app/database/`：PostgreSQL、migration、群消息 repository 和图片任务；不保存图片字节。
- `app/config/`：唯一配置模型、加载器、配置版本和目录监听。

插件必须声明稳定的 ASCII `plugin_id`。每个插件只获得绑定自身 ID 的类型化配置视图，不能通过公共接口读取启动配置或其他插件配置。插件私有关系数据使用 `plugin_<plugin_id>` schema、自有 migration 和类型化 repository；插件不直接持有 `AsyncSession`，也不通过通用 JSONB KV 保存状态。插件之间不导入、调用或订阅彼此，需要共用的能力放入公共模块。

## 失败策略

配置缺失、协议不一致、PostgreSQL 不可用或 migration 版本不匹配会直接失败。群消息持久化第二次失败后不再分发该事件，并以 1011 关闭当前 NapCat 会话。工具参数、回复标记或图片读取等可恢复错误会返回结构化信息，让模型或插件继续处理。

## 开发检查

```bash
uv lock --check
docker compose config --quiet
uv run pytest
uv run basedpyright
uv run python -m compileall app
git diff --check
```

`basedpyright` 必须保持 `0 errors, 0 warnings`。详细流程见 [运行架构](docs/runtime_architecture.md)。
