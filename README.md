# MyBot

MyBot 是面向 QQ 群聊场景的机器人服务。服务通过 FastAPI 承载 NapCat 反向 WebSocket 连接，使用 Redis 保存消息缓存和媒体索引，并通过 OpenAI Chat Completions 协议接入文本、工具调用与图片生成能力。

项目使用 Python 3.13+ 和 `uv` 管理依赖。配置、插件开关、日志目录和运行数据均与业务代码分离。

## 功能范围

- 接收 NapCat 反向 WebSocket 事件，并按事件类型分发给插件。
- 自动加载插件配置，使用 Pydantic 严格校验配置字段。
- 缓存群消息、引用消息、撤回事件和媒体文件，供插件在后续回合查询。
- 为 AI 群聊插件提供长期上下文、上下文压缩、工具调用循环和调试转储。
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

4. 启动服务：

```bash
uv run python -m app.main
```

NapCat 反向 WebSocket 地址示例：

```text
ws://<本机局域网 IP>:6055/ws/napcat
```

NapCat 侧的 Bearer Token 来自 `setting.toml` 中的 `[napcat].websocket_token`。

## Docker

```bash
docker compose up -d
```

默认挂载：

- `./setting.toml:/app/setting.toml`
- `./plugins_config:/app/plugins_config`
- `./data:/app/data`
- `./logs:/app/logs`
- `mybot-venv:/app/.venv`
- `mybot-uv-cache:/app/.uv-cache`

镜像提供 Python、uv/uvx、Node/npm/pnpm/yarn、Docker CLI、Git 和常用证书环境。容器启动时执行 `uv run --frozen --no-dev python -m app.main`，依赖版本由 `uv.lock` 固定，虚拟环境与 uv 缓存写入命名卷。

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

AI 群聊插件的主模型不支持图片输入时，需要配置多模态备用模型。当前消息或引用消息包含图片时，本轮正式回复请求会切到备用模型：

```toml
[ai_group_chat]
model_name = "deepseek-v4-pro"
model_vendors = "deepseek"
supports_multimodal = false
multimodal_fallback_model_name = "gpt-5.5"
multimodal_fallback_model_vendors = "openai"
```

## 运行边界

- `app/api/` 负责 NapCat Action 调用封装，不放插件业务逻辑。
- `app/models/` 负责 NapCat 入站事件、消息段和 JSON 边界模型。
- `app/services/napcat/` 负责可复用的 NapCat 本地工具集，工具说明写在工具 definition 和参数模型中。
- `app/plugins/` 负责编排具体业务流程，例如 AI 群聊、群通知、生图和撤回清理。
- `app/services/llm/` 负责模型服务路由、OpenAI 协议转换、工具注册和 MCP 工具适配。

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
├── database/             # Redis 消息存储和媒体缓存
├── models/               # NapCat 协议模型和 JSON 边界类型
├── plugins/              # 插件实现
│   ├── ai_group_chat/    # AI 群聊插件
│   ├── auto_unban/       # 自动解禁插件
│   ├── delete_recalled_message/
│   ├── group_notice/
│   └── image_generate/
├── services/
│   ├── llm/              # LLM 路由、OpenAI 协议转换、MCP 和工具注册
│   └── napcat/           # NapCat 本地工具集
└── utils/                # 日志、重试、文件和编码工具
```

## 开发检查

```bash
uv run basedpyright
uv run python -m unittest discover -s tests
uv run python -m compileall app
```

交付前需要保持 `uv run basedpyright` 输出 `0 errors, 0 warnings`。
