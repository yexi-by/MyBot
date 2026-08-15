# MyBot 运行架构

本文档描述 MyBot 运行时的职责边界、数据流、配置入口和失败策略，供接手维护时快速定位模块。

## 服务入口

`app.main` 读取 `setting.toml`，初始化日志系统，创建依赖容器，并启动 FastAPI 应用。FastAPI WebSocket 路由由 `NapCatServer` 注册，默认路径来自 `[server].websocket_path_prefix`，例如 `/ws/{client_id}`。

WebSocket 握手阶段会校验 NapCat Bearer Token。校验通过后，服务持续读取 NapCat 上报的 JSON 事件，并交给 `EventTypeChecker` 转换为协议模型。

## 事件处理流程

1. NapCat 通过反向 WebSocket 上报事件 JSON。
2. `EventTypeChecker` 解析事件类型，无法识别的事件会跳过处理。
3. `BOTClient` 保存机器人自身 QQ 号，并接收 Action 响应事件。
4. 入站群消息先在 PostgreSQL 短事务中写入，提交成功后才复制给 `EventDispatcher` 分发。
5. 群撤回通知先把目标消息标记为归档，再继续实时分发；其他事件不持久化。
6. `PluginController` 根据插件 `run(self, msg: EventType)` 的类型注解选择订阅插件。
7. 插件完成业务处理后，通过 `BOTClient` 调用 NapCat Action 发送消息、撤回消息或查询数据。

数据库写入失败时会在 250ms 后重试一次。第二次仍失败时不会分发对应事件，并以 1011 关闭当前 NapCat 会话。分发侧仍使用事件副本，避免插件修改协议对象影响其他插件。

## 数据与图片归档

PostgreSQL 只保存群消息、撤回字段和图片任务，不保存私聊、Meta、普通 Notice 或 Request。

- 核心查询列使用 B-tree 索引，异构消息段使用 JSONB；群号和 QQ 号始终按字符串保存。
- 入站和机器人出站群消息都会记录，重复事件通过复合唯一约束幂等处理。
- 撤回只设置撤回时间与操作者，正文和图片继续保存；普通 repository 查询统一排除撤回消息。
- 图片任务异步读取现有路径、URL 或 NapCat 刷新结果，校验后写入内容寻址文件；视频不下载。
- 出站 base64 图片在 NapCat 确认发送后主动写入内容寻址文件，不依赖后续回显才能保存。
- 图片任务状态保存在 PostgreSQL，进程中断后可以重新领取；图片字节只存在 `images/`。

群历史工具只读取 PostgreSQL，不向 NapCat 请求远端历史。最近消息、成员、时间范围和锚点前后文均由 SQL 查询完成；工具始终绑定触发事件的群号，调用参数不暴露 `group_id`。

插件使用稳定 `plugin_id`。插件私有表位于自己的 PostgreSQL schema，通过自有 migration 和类型化 repository 管理；插件业务对象不共享或长期持有 `AsyncSession`。

## LLM 与工具

`LLMHandler` 按 `model_vendors` 路由到具体模型服务。`OpenAIService` 负责把内部 `ChatMessage` 转换为 OpenAI Chat Completions 请求，并把模型返回的正文、工具调用和 reasoning 字段收敛为内部结构。

本地工具通过 `LLMToolRegistry` 注册。注册时使用 Pydantic 参数模型生成 JSON Schema，并按 OpenAI strict function 要求补齐 `required` 与 `additionalProperties`。

MCP 工具由 `MCPToolManager` 启动 stdio server 后加载。每个 MCP 工具暴露为 `mcp__{server}__{tool}`，结果会收敛为 JSON 可序列化结构。

## AI 群聊插件

`AIGroupChat` 在机器人被群消息艾特时运行。主要协作对象如下：

- `GroupChatMessageBuilder`：把当前群消息、引用消息和可读取图片整理为单条 LLM user 消息。
- `GroupChatToolLoop`：执行模型请求、工具调用、content 标记解析、群消息发送和进程内上下文写入。
- `GroupChatContextCompressor`：当请求预算超过上限时，把历史上下文整理为摘要，并与本轮消息组成新的 user 消息。
- `AIGroupChatDebugDumper`：按群写入进程内上下文 Markdown 增量，便于排查上下文变化；这些文件不用于启动恢复。

模型回复中的 `<Reply>` 与 `<At>QQ号</At>` 由 `NapCatMessageModifier` 解析。`<At>all</At>` 需要插件配置显式允许；关闭时会返回可恢复错误，工具循环会要求模型重写回复。

## 配置入口

- `setting.toml`：服务监听、NapCat Token、日志、网络、PostgreSQL、图片、LLM 和 MCP 配置。
- `plugins_config/plugins.toml`：插件开关与插件业务配置。
- `logs/ai_group_chat_debug/`：AI 群聊调试转储目录；Docker 中由可写的日志目录挂载提供。
- `images/`：永久保存的群图片目录。
- `logs/`：文本日志和结构化日志目录。

配置模型使用 `extra="forbid"`。未知字段会在启动或插件加载时暴露为校验错误。

## 失败策略

不可恢复问题直接抛错并写入文件日志，例如缺少必要配置、协议模型不满足工具调用约束、上下文压缩后仍超过预算、MCP 工具名重复。

可恢复问题会返回模型可理解的结构化结果或写入告警日志，例如工具参数错误、群消息 content 标记错误、图片读取失败、调试文件写入失败。此类问题不直接终止进程。

PostgreSQL 无法连接、migration 版本不一致或群消息事务失败属于不可恢复状态。启动时会拒绝就绪；运行中会停止分发对应消息并关闭当前 NapCat 会话。图片读取失败不会回滚消息，而是按数据库任务状态有限重试。

未知异常在终端输出中文摘要和定位字段，完整异常链写入文件日志。

## 验收命令

```bash
uv run basedpyright
uv run pytest
uv run python -m compileall app
```

修改 NapCat 本地工具集时，还需要做一次 fake bot 烟测，确认工具 schema、正常返回和可恢复错误都能被模型读取。
