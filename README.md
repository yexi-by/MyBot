# QQ Agent Harness

运行在 QQ 环境中的自主对话智能体框架。NapCat 提供 OneBot v11 协议接入与动作执行；Harness 统一管理模型路由、会话上下文、群级工具授权、持久化工作区与多模态扩展。默认示例人格为「耄耋」。

群聊与私聊消息仅作为上下文输入，智能体自主决定是否回应及调用何种工具。普通模型回复正文仅保留在内部上下文中，只有显式调用 `qq__send_message` 才会向 QQ 发送消息；主动静默时输出 `本轮不发言。`。

## 核心机制

### 会话与权限模型

- **会话隔离与唤醒**：以 `机器人账号 + 群/私聊 + 群号/私聊对象` 为会话标识。群聊通过 `@机器人`、引用机器人消息或命中关键词唤醒，同一群成员共享该群会话；私聊启用后逐条唤醒。
- **群级授权**：权限仅按群或统一私聊策略配置（分类开关、单工具覆盖、参数约束、`allow_at_all`、跨群/跨私聊目标白名单），不设个人角色特权。WebUI 支持跨会话复制或按模块批量应用配置。
- **有界内存状态**：会话上下文驻留内存（默认上限 `max_retained_sessions = 100`、`max_retained_bytes = 256 MiB`，按 LRU 回收空闲会话，进程重启清空）。WebUI「上下文」页可实时查看各轮输入、内部正文、工具调用轨迹与压缩摘要，支持随时停止当前轮次或重置上下文。

### 模型路由与上下文管理

- **连接与模型解耦**：`[providers.<id>]` 仅配置连接（`responses` 或 `chat_completions` 协议、`base_url`、密钥、代理、SSE 流式及空闲超时 `idle_timeout`、自动重试 `max_retries`）；各会话独立配置会话模型、`reasoning_effort`、输入模态与上下文预算 `max_context_tokens`（默认 `128000`）。
- **轮内减压与压缩**：请求前结合本地估算与上游返回 `usage` 校准上下文占用。达到预算 80% 时，先执行零模型开销的两级前置减压（裁剪历史超 8 KB 工具回执头尾 → 将历史内联图片/音频卸载为占位说明）；仍超阈值时调用会话模型生成第一人称前情记忆摘要（`<session_memory>`），压缩至 `compaction_target_percent`（默认 `50%`）。
- **闲时压缩**：启用 `idle_compaction` 后，会话闲置满 `after_minutes` 且可压缩内容达 `min_tokens` 时，后台使用指定轻量模型将人格与最新知识库以外的历史合并为单份摘要，避免提示缓存过期后全量重计费。压缩期间若收到新唤醒消息，立即中断压缩并保留原文处理新消息。
- **执行调度**：单轮不设模型请求次数或消息发送次数硬上限；同批连续只读工具自动并发执行；轮次执行期间同会话新到达的唤醒消息在步骤边界实时并入；内置重复相同报错防死循环拦截。

### 能力与工具目录

工具参数 Schema 与权限控制共用同一定义。每次请求固定排序提供常驻工具并按解锁顺序追加扩展工具，保护上游前缀缓存。

| 分类 / 配置段 | 核心工具 | 能力说明 |
| :--- | :--- | :--- |
| **QQ 基础与扩展**<br>(`messages` / `members` / `groups` 等) | `qq__send_message`、`qq__read_media`、`qq__save_media_to_workspace`、`qq__get_history`、`qq__search_tools` 等（共 106 个工具） | 高频工具默认常驻；禁言、头衔、公告、待办、相册、群文件、收藏表情等扩展工具通过 `qq__search_tools` 按关键词或分类检索加载。媒体工具支持读取消息附件、用户/群头像及官方图床图片，合并转发与成员列表支持筛选分页。完整动作映射见 WebUI「工具」页。 |
| **共享工作区**<br>(`[workspace]`) | `workspace__read_file` / `write_file` / `edit_file` / `list_files` / `search_files` / `delete_path` / `exec` | 单容器持久化共享目录（默认软上限 50 GB），支持 UTF-8 字节分页读写、精确片段替换、正则搜索及洁净环境变量子进程执行。详见 [共享工作区说明](docs/workspace.md)。 |
| **后台子代理**<br>(`subagent`) | `subagent__spawn` / `list` / `inspect` / `send` / `cancel` | 异步派生独立上下文后台协程处理长耗时分析或检索，继承当前会话策略（剥离对外发言与群管写操作），支持进度巡检、轨迹抽查、运行中实时纠偏、原上下文续跑及完成后主动唤醒主会话。 |
| **按需技能**<br>(`skills`) | `skill__load` | 自动注入 `config/skills/` 下的技能摘要索引，按需读取领域作业规范。 |
| **定时提醒**<br>(`timer`) | `timer__schedule` / `list` / `cancel` | 有界内存定时唤醒任务（重启清空）。 |
| **图像生成**<br>(`[image]`) | `image__generate` | 支持文生图与结合会话图片/头像/工作区图片的改图；兼容 `openai_images`、`chat_completions`、`responses`、`gemini`、`xai_images`、`ark_images`、`dashscope` 七种协议，WebUI 支持端点预览与在线测试。 |
| **语音合成**<br>(`[tts]`) | `tts__synthesize` | 对接独立 IndexTTS 2.5 HTTP 服务（见 [IndexTTS 接入说明](deploy/indextts/README.md)），固定主音色，支持 `reference` / `text` / `vector` / `audio` 四种情绪模式；WebUI 支持显存管理、音色上传与在线试听。 |
| **MCP 扩展**<br>(`[mcp.<id>]`) | `<server>__*` | 支持 `stdio` 与 `streamable_http` 接入外部 MCP 服务，按服务分类、具体工具及参数规则授权。 |

## 快速开始

### 本地开发

依赖 Python 3.14、`uv`、Node.js 24 与 `npm`。

```powershell
uv sync --locked
Copy-Item -Recurse config.example config   # 仅首次初始化本地配置时执行
uv run --locked python -m qq_agent_harness --config config/harness.toml
```

- **WebUI 与接入端口**：默认监听 `6055`（同时承载控制台页面、API 与 NapCat 接入）。前端独立开发可执行 `npm --prefix webui ci && npm --prefix webui run dev`（默认将 `/api` 代理至 `6056`，后端配合 `--port 6056` 启动）。
- **NapCat 配置**：使用反向 WebSocket 连接 `ws://<harness-host>:6055/napcat/ws`，消息格式设为 `array`，填写与 `napcat.token` 一致的 Bearer Token（空 Token 拒绝接入）。接口清单锁定 NapCat `v4.18.41`。
- **控制台鉴权与配置写回**：可在「设置 → 控制台服务」配置 `server.auth_token` 启用登录口令保护。TOML 配置修改自动增量保存（`Ctrl+S` 立即保存），保留注释并校验文件乐观锁；群权限实时生效，人格与知识库下一轮生效，接入与并发拓扑变更在页面顶部提示一键重启生效。

### 单容器部署

单镜像内置后端、WebUI 静态产物与工作区工具链（Python 3.14、`uv`、Node.js 24、Git、FFmpeg、ripgrep/fd/jq、Poppler、CJK/Emoji 字体及常用数据处理/绘图库），以非特权 `1000:1000` 用户运行。

```bash
docker build -t qq-agent-harness:1.0.0 .
HARNESS_IMAGE=qq-agent-harness:1.0.0 WORKSPACE_DIR=/data/mybot-workspace docker compose up -d
```

## 开发验证

```powershell
uv run --locked python -m ruff check
uv run --locked python -m ruff format --check
uv run --locked python -m basedpyright
uv run --locked python -m pytest
npm --prefix webui run build
```
