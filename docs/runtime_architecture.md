# MyBot 运行架构

本文说明 MyBot 的运行流程、配置更新边界和失败语义。

## 启动与配置

`app.main` 从 `config/mybot.toml` 创建 APP 级 `ConfigManager`。它完成完整 TOML 校验、LLM provider 引用校验，并把插件引用的 prompt、知识库和通用要求读入不可变配置快照。随后日志、网络客户端、PostgreSQL、LLM provider、MCP 和 FastAPI 使用启动配置创建。

应用启动后，`ConfigWatcher` 监听 `config/`。它只处理 `mybot.toml` 和当前插件配置引用的文件，并按 `server.config_watch_debounce_ms` 与 `server.config_watch_step_ms` 处理连续变化。每次加载都重新校验完整文件：失败时保留旧快照；成功时只发布新的插件配置快照。启动配置变化只记录需要重启的节，不修改已经创建的资源。

插件只持有按自身 `plugin_id` 绑定的 `PluginConfigView`，不能通过公共接口读取完整启动配置或其他插件配置。处理事件开始时，插件取得当前版本并把对应运行对象保存在局部变量；本轮不会被后续配置变化影响，下一条相关事件使用新版本。配置节不存在时，插件仍完成注册，但不会处理事件。

## 事件处理

1. NapCat 连接 `/ws/{client_id}`，握手阶段校验 Bearer Token。
2. `EventTypeChecker` 把 JSON 转换为协议模型。
3. 群消息先用 PostgreSQL 短事务保存，提交成功后才分发。
4. 群撤回先写入撤回时间和操作者，再实时分发；其他 Notice、Meta、Request 和私聊不持久化。
5. `PluginController` 根据 `run(self, msg: EventType)` 的直接类型注解选择插件，并按优先级调用。插件返回 `True` 后停止向较低优先级插件分发。
6. 插件通过 `BOTClient` 调用 NapCat Action。

数据库写入失败后按 `database.persistence_retry_delays_seconds` 的顺序等待并重试；空列表表示不重试。全部尝试失败时，不分发该事件，并以 1011 关闭当前 NapCat 会话。出站消息已经由 NapCat 成功发送后若记录失败，不伪造发送失败，但同样把会话标记为不健康并停止继续处理。

`PluginController` 不是插件内部事件总线。插件只能使用 `Context` 中的公共服务和 repository，不得导入、查找、调用或订阅其他插件。

## 群消息与媒体

PostgreSQL 保存入站和出站群消息、撤回字段及顶层图片和视频任务：

- 群、机器人和消息 ID 使用字符串；历史以 `(occurred_at, id)` 稳定排序。
- 普通查询只返回未撤回消息。撤回原文和媒体永久保留，但普通引用、历史和 AI 工具均视为不存在。
- 历史、成员筛选、时间范围和锚点前后文都由 SQL 查询，并严格绑定当前机器人和群。
- 媒体 worker 依次尝试已有路径、URL 和 NapCat 刷新，校验实际媒体内容后写入 SHA-256 内容寻址文件。
- 图片与视频任务共用 `core.group_message_media` 和数据库租约，支持进程中断后继续处理；文件共用 `storage.images` 的持久化目录及归档参数。读取引用消息时，仓库将归档路径补回对应消息段。
- 出站 base64 图片在发送成功后直接归档，媒体字节不进入 PostgreSQL。

## 插件与数据库

插件使用稳定 `plugin_id`，通过 `GroupMessageReader` 读取当前机器人和群的核心消息，写入由运行期核心持久化服务负责。业务代码不接触原始 `AsyncSession`。

当前插件被视为可信的本地代码；单一数据库用户提供的是代码接口边界，不是不可信插件的权限沙箱。

## LLM、MCP 与本地工具

`LLMHandler` 按 provider ID 查找启动时创建的服务。`LLMProviderConfig.api_format` 明确选择 `chat_completions` 或 `responses`，分别创建 `ChatCompletionsService` 与 `ResponsesService`；插件通过 `{ provider, name }` 选择模型。两个适配器分别转换 `ChatMessage`、图片和工具调用，不自动切换协议。

Chat Completions 从 `message.content` 提取正式回复，缺少正文时读取 `message.refusal`，不猜测附加的 reasoning 字段；工具调用的 `extra_content` 原样传回以保留签名。Responses 将文字、图片、函数调用及结果转换成各自的输入项；以 `store=False` 执行请求，保留本轮原始输出项用于工具续问，并请求 `reasoning.encrypted_content` 以维持协议状态，不使用 `previous_response_id`。这些原始输出项不作为群回复或长期上下文保存。

模型响应的结构或内容异常会直接结束本轮，不追加纠错提示；只有网络连接失败、超时、限流和 HTTP 5xx 按配置重试，重试输入保持不变。模型请求最终失败由 `LLMHandler` 转换为 `LLMRequestError`，反馈只包含错误分类和状态码，异常链不携带供应商原始响应。重试日志记录尝试次数及异常类型，日志诊断不打印局部变量。AI 群聊在一次请求边界向当前消息反馈预期失败；代码缺陷继续向外报告。

本地工具由 `LLMToolRegistry` 注册。NapCat 群聊工具绑定当前事件的机器人和群，不允许模型传入其他群号。`app/api/` 保留运行期实际使用的 Action 封装，工具层负责业务组合。MCP manager 连接配置中的 stdio 或 Streamable HTTP server，并以 `mcp__{server}__{tool}` 暴露工具。

MCP 每个服务的初始化和工具调用分别受 `initialization_timeout_seconds`、`call_timeout_seconds` 限制。初始化失败释放已经建立的资源；工具调用超时返回结构化错误。结构化工具输出以 `{is_error, data}` 传递，保留 MCP 协议中的失败标记。

AI 群聊由以下组件组成：

- `GroupChatMessageBuilder`：把当前消息整理为聊天记录行（时间、发送者、消息 ID、引用块、正文），并读取引用和图片。配置 `recent_chat` 时，经 `GroupMessageReader.list_around` 读取当前消息之前的群聊，只保留 `ConversationContextStore.started_at` 之后的消息，由 `RecentChatFormatter` 整理为临时记录；该记录只进入本轮请求，不进入长期上下文。
- 图片读取服务按数量、大小、宽高和 MIME 配置校验原图，超限时只执行 `error` 或 `skip`。通过读取的原始字节直接进入主模型请求，图片是否进入长期上下文由 `retain_images` 控制。
- 视频输入仅用于当前轮，适用于支持 `video_url` 的 Chat Completions 兼容服务；Responses 遇到视频输入时明确报错，不转码或降级。启用视频但本轮没有视频时，文字与图片仍可正常发送。
- `GroupChatToolLoop`：执行主模型、工具、回复标记解析和消息发送。系统提示词固定按人格、知识库、通用要求组成。无效 `<Reply>`、`<At>` 标记会把发送失败事实返回模型继续处理，不附加重写指令。
- `max_context_tokens` 只约束长期上下文：每轮开始检查已保存的系统提示词与历史；超限时压缩历史，摘要仍过长则继续缩减。工具定义、本轮输入（含最近群聊记录）、工具结果、图片及压缩请求属于临时上下文，可超出该预算。工具结果完整交付，按配置保存后若使历史超限，下一轮再压缩。
- `GroupChatContextCompressor`：把历史整理为独立摘要，并保留当前输入的消息与图片边界。空摘要或无法缩短的摘要会反馈失败，原历史保留供后续重试；压缩通知发送失败只记录日志。Provider 自身的上下文窗口限制继续生效。

AI 插件为每个群保留一把只保护长期上下文快照、提交和重置的 `asyncio.Lock`。同群事件使用各自的临时上下文并发执行，完成后短暂持锁，按完成顺序提交整轮消息。并发压缩只能在其基础版本未变化时替换历史，否则只追加当前轮，不能覆盖先完成的请求。system prompt、知识库或通用要求变化后，旧配置下尚未完成的请求不会写入新上下文；其他配置变化保留上下文，并在下一轮使用新值。

## 目录

- `config/mybot.toml`：唯一运行配置。
- `config/`：插件引用的 prompt 和知识库。
- `config.example/`：示例配置引用的默认提示词（默认人格「耄耋」），结构与 `config/` 一致，复制后使用；不作为已部署环境的配置来源。
- `webui/`：配置控制台前端工程（React + TypeScript + Vite，原生 CSS 主题变量，CodeMirror 6 编辑文本），构建产物由 FastAPI 伺服。
- `app/webui/`：WebUI 后端（`/api/*` 配置与文本文件路由、tomlkit 保注释写回、SPA 挂载）。
- `images/`：永久群图片与视频。
- `logs/`：结构化运行日志。

WebUI 与主服务同端口，不另建配置状态。配置表单停止编辑 800ms 后自动校验并写回，文本文件停止编辑 1 秒后自动写回；每次请求都使用读取时的内容哈希，外部修改发生后不会被静默覆盖。文件接口允许访问 `config/` 内任意扩展名的 UTF-8 文本，因此可在 TOML 语法损坏时直接修复 `mybot.toml`；二进制文件和目录逃逸仍被拒绝。Markdown 文件使用语法高亮编辑器，并可并排实时预览 GFM 渲染结果。生产 Compose 允许 MyBot 写入配置目录，migration 服务仍使用只读挂载。

前端以 `webui/src/schema/` 的声明式字段定义描述每个配置节（类型、单位、取值范围、可选节开关和字段联动），同一份定义生成表单、做客户端预检、供命令面板搜索，并给写回记录提供中文字段名。整份配置草稿放在单一 store 里，按叶子路径与上次写入的版本比较，得出字段级和页面级改动；保存失败时按 API 返回的 `path` 把错误标到字段上，只有点分 `location` 的文件与模型引用错误由后端还原出 `path`。插件配置写入后，前端轮询 `meta.plugin_revision`，增长即视为热生效；启动配置节只提示待重启。

文本页首次访问后保留单个编辑会话，切换配置页面不会取消其自动保存。顶栏状态和 `Ctrl+S` 作用于当前编辑对象，关闭页面及电源操作共同检查所有未保存内容。

WebUI 还提供 `POST /api/system/restart` 与 `POST /api/system/shutdown` 电源端点：`PowerController` 按 `server.power_action_delay_seconds` 延迟触发 uvicorn 优雅停机，重启/关机在进程级行为一致，是否重新拉起由外部守护策略决定（`docker-compose.yml` 的 mybot 服务是 `restart: unless-stopped`，容器内两种操作都会被重新拉起）。前端以 `/api/config` 元信息中的 `boot_id` 变化判定新进程已接管。

## 关闭顺序

应用关闭时先通知配置 watcher 停止并按配置超时等待任务退出，再关闭 MCP、LLM 客户端、HTTP 客户端、PostgreSQL runtime 和依赖容器。WebSocket 会话结束时按 `plugin_execution.stop_timeout_seconds` 停止插件消费者，并按图片存储配置停止该机器人对应的图片 worker。

## 验收

```bash
uv lock --check
docker compose config --quiet
uv run pytest
uv run basedpyright
uv run python -m compileall app
cd webui && npm run build
git diff --check
```
