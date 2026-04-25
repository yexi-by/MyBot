# MyBot

基于 FastAPI、NapCat、Redis 和 OpenAI 兼容 LLM 接口的 QQ 机器人框架，使用 Python 3.13+ 与 `uv` 管理依赖。

## 当前能力

- NapCat 反向 WebSocket 服务，默认监听 `0.0.0.0:6055`。
- 插件自动注册与按事件类型分发。
- Redis 消息缓存、撤回同步清理和媒体缓存。
- OpenAI 兼容 LLM 文本、工具调用、图片接口。
- MCP stdio 工具接入。
- 通用 NapCat 群聊本地工具集，包括回复、艾特、群文件查询和群历史查询。

## 本地运行

1. 安装依赖：

```bash
uv sync
```

2. 准备配置：

```bash
cp setting.example.toml setting.toml
```

3. 按需编辑 `setting.toml` 和 `plugins_config/plugins.toml`。

4. 启动服务：

```bash
uv run python -m app.main
```

NapCat 反向 WebSocket 地址示例：

```text
ws://<本机局域网 IP>:6055/ws/napcat
```

Token 使用 `setting.toml` 中的 `password`，NapCat 侧填写 Bearer Token 对应值即可。

## Docker

```bash
docker compose up -d
```

默认挂载：

- `./setting.toml:/app/setting.toml`
- `./plugins_config:/app/plugins_config`
- `./data:/app/data`
- `./logs:/app/logs`

镜像内预装 Python、Node.js、npm、pnpm、yarn、uv/uvx、Docker CLI 和常用 MCP stdio 运行环境。

## 配置说明

全局配置放在 `setting.toml`，示例见 `setting.example.toml`。

插件配置放在 `plugins_config/plugins.toml`。插件配置由各插件自己的 Pydantic 模型严格校验，未知字段会直接报错，避免配置拼错后静默失效。

LLM 当前只维护 OpenAI 兼容协议面：

```toml
[[llm_settings]]
api_key = "sk-xxx"
base_url = "https://api.deepseek.com"
model_vendors = "deepseek"
provider_type = "openai"
retry_count = 3
retry_delay = 1
```

## 代码结构

```text
app/
├── api/                  # NapCat WebSocket Action 封装
├── config/               # 全局配置和插件配置加载
├── core/                 # FastAPI 服务、DI、事件分发、插件控制器
├── database/             # Redis 消息存储和媒体缓存
├── models/               # NapCat 协议模型和 JSON 边界类型
├── plugins/              # 插件实现
│   ├── ai_group_chat/    # AI 智能群聊回复插件
│   ├── auto_unban/       # 自动解禁插件
│   ├── delete_recalled_message/
│   ├── group_notice/
│   └── image_generate/
├── services/
│   ├── llm/              # OpenAI 兼容 LLM、MCP、工具注册
│   └── napcat/           # 通用 NapCat 本地工具集
└── utils/                # 日志、重试、文件等通用工具
```

## 开发检查

```bash
uv run basedpyright
uv run python -m unittest discover -s tests
uv run python -m compileall app
```

项目使用 `basedpyright` strict 模式，提交前需要保持 `0 errors, 0 warnings`。
