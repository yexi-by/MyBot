# MyBot 运行架构

本文档描述 MyBot 运行时的职责边界、数据流、配置入口和失败策略，供接手维护时快速定位模块。

## 服务入口

`app.main` 读取 `setting.toml`，初始化日志系统，创建依赖容器，并启动 FastAPI 应用。FastAPI WebSocket 路由由 `NapCatServer` 注册，默认路径来自 `[server].websocket_path_prefix`，例如 `/ws/{client_id}`。

WebSocket 握手阶段会校验 NapCat Bearer Token。校验通过后，服务持续读取 NapCat 上报的 JSON 事件，并交给 `EventTypeChecker` 转换为协议模型。

## 事件处理流程

1. NapCat 通过反向 WebSocket 上报事件 JSON。
2. `EventTypeChecker` 解析事件类型，无法识别的事件会跳过处理。
3. `BOTClient` 保存机器人自身 QQ 号，并接收 Action 响应事件。
4. 非响应事件会复制一份交给 `EventDispatcher` 异步分发，原始事件继续进入 Redis 写入队列。
5. `PluginController` 根据插件 `run(self, msg: EventType)` 的类型注解选择订阅插件。
6. 插件完成业务处理后，通过 `BOTClient` 调用 NapCat Action 发送消息、撤回消息或查询数据。

事件分发和 Redis 入库并行执行。分发侧使用事件副本，避免插件修改模型对象影响缓存内容。

## 数据与缓存

`RedisDatabaseManager` 负责消息缓存、时间索引、媒体下载和出站消息回写。

- 入站群消息写入 Redis Hash，并在 ZSet 中记录时间戳索引。
- 图片和视频消息会保存本地路径，读取失败时清空对应媒体段的本地路径。
- 机器人主动发送的消息会回写到 Redis，供后续引用消息查询。
- 撤回事件会删除 Redis 中对应消息和时间索引。

群历史工具只读取本地 Redis 缓存，不向 NapCat 请求远端历史。工具始终绑定触发事件的群号，调用参数不暴露 `group_id`。

## LLM 与工具

`LLMHandler` 按 `model_vendors` 路由到具体模型服务。`OpenAIService` 负责把内部 `ChatMessage` 转换为 OpenAI Chat Completions 请求，并把模型返回的正文、工具调用和 reasoning 字段收敛为内部结构。

本地工具通过 `LLMToolRegistry` 注册。注册时使用 Pydantic 参数模型生成 JSON Schema，并按 OpenAI strict function 要求补齐 `required` 与 `additionalProperties`。

MCP 工具由 `MCPToolManager` 启动 stdio server 后加载。每个 MCP 工具暴露为 `mcp__{server}__{tool}`，结果会收敛为 JSON 可序列化结构。

## AI 群聊插件

`AIGroupChat` 在机器人被群消息艾特时运行。主要协作对象如下：

- `GroupChatMessageBuilder`：把当前群消息、引用消息和可读取图片整理为单条 LLM user 消息。
- `GroupChatToolLoop`：执行模型请求、工具调用、content 标记解析、群消息发送和长期上下文写入。
- `GroupChatContextCompressor`：当请求预算超过上限时，把历史上下文整理为摘要，并与本轮消息组成新的 user 消息。
- `AIGroupChatDebugDumper`：按群写入长期上下文 Markdown 增量，便于排查上下文变化。

模型回复中的 `<Reply>` 与 `<At>QQ号</At>` 由 `NapCatMessageModifier` 解析。`<At>all</At>` 需要插件配置显式允许；关闭时会返回可恢复错误，工具循环会要求模型重写回复。

## 配置入口

- `setting.toml`：服务监听、NapCat Token、日志、网络、Redis、LLM 和 MCP 配置。
- `plugins_config/plugins.toml`：插件开关与插件业务配置。
- `plugins_config/ai_group_chat_debug/`：AI 群聊调试转储目录。
- `data/`：媒体缓存目录。
- `logs/`：文本日志和结构化日志目录。

配置模型使用 `extra="forbid"`。未知字段会在启动或插件加载时暴露为校验错误。

## 失败策略

不可恢复问题直接抛错并写入文件日志，例如缺少必要配置、协议模型不满足工具调用约束、上下文压缩后仍超过预算、MCP 工具名重复。

可恢复问题会返回模型可理解的结构化结果或写入告警日志，例如工具参数错误、群消息 content 标记错误、图片读取失败、调试文件写入失败。此类问题不直接终止进程。

未知异常在终端输出中文摘要和定位字段，完整异常链写入文件日志。

## 验收命令

```bash
uv run basedpyright
uv run python -m unittest discover -s tests
uv run python -m compileall app
```

修改 NapCat 本地工具集时，还需要做一次 fake bot 烟测，确认工具 schema、正常返回和可恢复错误都能被模型读取。
