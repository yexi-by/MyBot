# MyBot 运行架构

本文说明 MyBot 的运行流程、配置更新边界和失败语义。

## 启动与配置

`app.main` 从 `config/mybot.toml` 创建 APP 级 `ConfigManager`。它完成完整 TOML 校验、LLM provider 引用校验，并把插件引用的 prompt、知识库和通用要求读入不可变配置快照。随后日志、网络客户端、PostgreSQL、LLM provider、MCP 和 FastAPI 使用启动配置创建。

应用启动后，`ConfigWatcher` 监听 `config/`。它只处理 `mybot.toml` 和当前插件配置引用的文件，并把 500ms 内连续变化合并为一次加载。每次加载都重新校验完整文件：失败时保留旧快照；成功时只发布新的插件配置快照。启动配置变化只记录需要重启的节，不修改已经创建的资源。

插件只持有按自身 `plugin_id` 绑定的 `PluginConfigView`，不能通过公共接口读取完整启动配置或其他插件配置。处理事件开始时，插件取得当前版本并把对应运行对象保存在局部变量；本轮不会被后续配置变化影响，下一条相关事件使用新版本。配置节不存在时，插件仍完成注册，但不会处理事件。

## 事件处理

1. NapCat 连接 `/ws/{client_id}`，握手阶段校验 Bearer Token。
2. `EventTypeChecker` 把 JSON 转换为协议模型。
3. 群消息先用 PostgreSQL 短事务保存，提交成功后才分发。
4. 群撤回先写入撤回时间和操作者，再实时分发；其他 Notice、Meta、Request 和私聊不持久化。
5. `PluginController` 根据 `run(self, msg: EventType)` 的直接类型注解选择插件，并按优先级调用。插件返回 `True` 后停止向较低优先级插件分发。
6. 插件通过 `BOTClient` 调用 NapCat Action。

数据库写入失败后等待 250ms 重试一次。第二次仍失败时，不分发该事件，并以 1011 关闭当前 NapCat 会话。出站消息已经由 NapCat 成功发送后若记录失败，不伪造发送失败，但同样把会话标记为不健康并停止继续处理。

`PluginController` 不是插件内部事件总线。插件只能使用 `Context` 中的公共服务和 repository，不得导入、查找、调用或订阅其他插件。

## 群消息与图片

PostgreSQL 保存入站和出站群消息、撤回字段及顶层图片任务：

- 群、机器人和消息 ID 使用字符串；历史以 `(occurred_at, id)` 稳定排序。
- 普通查询只返回未撤回消息。撤回原文和图片永久保留，但普通引用、历史和 AI 工具均视为不存在。
- 历史、成员筛选、时间范围和锚点前后文都由 SQL 查询，并严格绑定当前机器人和群。
- 图片 worker 依次尝试已有路径、URL 和 NapCat 刷新，校验实际图片内容后写入 SHA-256 内容寻址文件。
- 图片任务通过数据库租约支持进程中断后继续处理。视频只保留消息段，不下载。
- 出站 base64 图片在发送成功后直接归档，图片字节不进入 PostgreSQL。

## 插件与数据库

插件使用稳定 `plugin_id`。核心群消息由窄接口读取和写入；插件不能写核心表。需要私有数据的插件在 `plugin_<plugin_id>` schema 中维护自己的表、Alembic migration 和类型化 repository。`Context.create_repository(...)` 只提供绑定插件身份的短生命周期 session factory，业务代码不接触原始 `AsyncSession`。

当前插件被视为可信的本地代码；单一数据库用户提供的是代码接口边界，不是不可信插件的权限沙箱。

## LLM、MCP 与本地工具

`LLMHandler` 按 provider ID 查找启动时创建的 OpenAI 兼容服务。插件通过 `{ provider, name }` 选择模型。`OpenAIService` 负责转换 `ChatMessage`，并把正文、工具调用和 reasoning 收敛为内部结构。

本地工具由 `LLMToolRegistry` 注册。NapCat 群聊工具绑定当前事件的机器人和群，不允许模型传入其他群号。MCP manager 启动配置中的 stdio server，并以 `mcp__{server}__{tool}` 暴露工具。

AI 群聊由以下组件组成：

- `GroupChatMessageBuilder`：读取当前消息、引用和图片。
- `VisionDescriptionTool`：主模型不支持图片时，生成与问题相关的事实描述。
- `GroupChatToolLoop`：执行主模型、工具、回复标记解析和消息发送。
- `GroupChatContextCompressor`：请求超预算时压缩历史。
- `AIGroupChatDebugDumper`：向 `logs/ai_group_chat_debug/` 写调试记录，不参与恢复。

AI 插件为每个群保留一把只保护长期上下文快照、提交和重置的 `asyncio.Lock`。同群事件使用各自的临时上下文并发执行，完成后短暂持锁，按完成顺序提交整轮消息。并发压缩只能在其基础版本未变化时替换历史，否则只追加当前轮，不能覆盖先完成的请求。system prompt、知识库或通用要求变化后，旧配置下尚未完成的请求不会写入新上下文；其他配置变化保留上下文，并在下一轮使用新值。

## 目录

- `config/mybot.toml`：唯一运行配置。
- `config/`：插件引用的 prompt 和知识库。
- `webui/`：配置控制台前端工程（React + Vite），构建产物由 FastAPI 伺服。
- `app/webui/`：WebUI 后端（`/api/*` 配置与文本文件路由、tomlkit 保注释写回、SPA 挂载）。
- `images/`：永久群图片。
- `logs/`：日志和 AI 调试转储。

WebUI 与主服务同端口，不另建配置状态。配置表单停止编辑 800ms 后自动校验并写回，文本文件停止编辑 1 秒后自动写回；每次请求都使用读取时的内容哈希，外部修改发生后不会被静默覆盖。文本文件接口只允许访问 `config/` 内的 `.md` 和 `.txt`；Markdown 文件使用语法高亮编辑器，并可并排实时预览 GFM 渲染结果。生产 Compose 允许 MyBot 写入配置目录，migration 服务仍使用只读挂载。

WebUI 还提供 `POST /api/system/restart` 与 `POST /api/system/shutdown` 电源端点：`PowerController` 延迟触发 uvicorn 优雅停机，重启/关机在进程级行为一致，是否重新拉起由外部守护策略决定（`docker-compose.yml` 的 mybot 服务是 `restart: unless-stopped`，容器内两种操作都会被重新拉起）。

## 关闭顺序

应用关闭时先通知配置 watcher 停止并等待任务退出，再关闭 MCP、HTTP 客户端、PostgreSQL runtime 和依赖容器。WebSocket 会话结束时停止插件消费者和该机器人对应的图片 worker。

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
