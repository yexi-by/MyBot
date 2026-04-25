# MyBot 开发规范

## NapCat 本地工具集规范

NapCat 本地工具集是把 QQ/NapCat 能力暴露给 AI 工具调用的通用服务层。它不是插件私有逻辑，也不是 LLM provider 的一部分。

### 目录边界

- NapCat 本地工具集统一放在 `app/services/napcat/`。
- 群聊工具放在 `app/services/napcat/group_tools.py`。
- 后续新增私聊、群管理、消息管理等工具时，优先在 `app/services/napcat/` 下按业务边界拆分文件。
- `app/api/` 只保留 NapCat 原始 API 调用封装。
- `app/models/` 只保留协议模型。
- 插件只能组合和配置工具集，不要在插件内手写工具 schema。

### 工具暴露原则

- 工具说明必须写在工具自身的 `description` 和 Pydantic 参数模型 `Field(description=...)` 中。
- 禁止把工具使用说明硬塞进角色提示词或插件 prompt，避免污染长期上下文。
- 工具名使用稳定前缀 `qq__`，例如 `qq__mention_user`、`qq__reply_current_message`。
- MCP 工具使用 `mcp__{server}__{tool}`，本地 NapCat 工具不得占用 `mcp__` 前缀。
- 信息工具只返回信息，不发送群消息。
- 消息修饰工具只登记本轮最终消息的修饰动作，不立即发送正文。
- 历史对话、引用消息详情等本地缓存类能力，优先读取 Redis，不要直接请求 NapCat 远端历史。

### 群聊安全边界

- 群聊工具必须绑定当前触发事件 `GroupMessage`，默认使用 `event.group_id`。
- 工具参数中不要暴露 `group_id`，避免 AI 指定其他群导致串群。
- 读取群聊历史时同样只能读取当前 `event.group_id` 对应的 Redis 缓存。
- 群聊历史工具应支持按最近条数、最近分钟数、明确起止时间范围查询；时间范围必须使用清晰的北京时间格式，避免自然语言日期解析歧义。
- 如果未来确实需要跨群能力，必须单独设计权限配置和审计日志，不得复用当前群聊工具。
- `@全体` 必须由插件配置开关控制。即使关闭，也要在工具 schema 中保留 `"all"` 的合法参数形态，让 AI 能看到真实能力边界。
- 当 `@全体` 被关闭时，工具应返回结构化错误结果给 AI，而不是在参数校验层隐藏该能力。

### 错误返回

- 工具调用失败应返回可供模型理解的结构化结果，例如：

```json
{
  "ok": false,
  "is_error": true,
  "error_type": "ValueError",
  "error": "具体错误原因",
  "message": "工具调用失败。请根据错误信息修正参数或改用其他回复方式。"
}
```

- 可恢复错误优先回填给 AI 继续本轮对话，不要直接让插件崩溃。
- 真正的代码缺陷、配置缺失、协议不一致等不可恢复错误应 fail-fast，并在日志中暴露。

### 工具结果与上下文

- 工具结果是否进入长期上下文必须由插件配置控制。
- 默认不要把大体积工具结果写入长期上下文，避免上下文膨胀。
- 如果开启持久化，应优先保存对后续对话有用的摘要或结构化结果，不要无脑保存大段原始响应。

### 类型与模型

- NapCat 官方文档中标注为字符串或整数均可的 ID 字段，项目内部统一收敛为字符串。
- 工具参数应使用明确的 Pydantic 模型，禁止用裸 `dict` 当参数 schema。
- 工具返回值必须是 JSON 可序列化结构。
- 禁止为了兼容旧插件保留转发门面；路径变更后直接修改导入。

### 插件使用方式

- 插件通过 `NapCatGroupToolExecutor` 获取本地 QQ 工具。
- 插件负责配置开关和工具循环策略，例如：
  - 是否允许 `@全体`
  - 工具结果是否进入长期上下文
  - 消息修饰工具是否必须同轮携带非空 `content`
- 插件不负责描述每个工具怎么用，这些信息必须来自工具自身定义。

### 插件事件类型注解

- 插件管理器会通过 `run(self, msg: EventType)` 的类型注解建立事件路由。
- 当插件需要订阅多个事件时，必须在 `BasePlugin[...]` 和 `run` 参数上直接写事件联合类型，例如：

```python
class DemoPlugin(BasePlugin[GroupMessage | GroupRecallNoticeEvent]):
    async def run(self, msg: GroupMessage | GroupRecallNoticeEvent) -> bool:
        ...
```

- 禁止把事件联合类型先写成 `type EventInput = A | B` 再用于 `BasePlugin[EventInput]` 或 `run(..., msg: EventInput)`。
- 原因是当前插件管理器只解析直接事件类和直接联合类型；Python 3.13 的 `type` 别名会以 `TypeAliasType` 形态进入类型解析，启动后 NapCat WebSocket 连接会被服务端异常关闭。

### 验收要求

- 修改 NapCat 本地工具集后必须运行：

```powershell
uv run basedpyright
uv run python -m compileall app
```

- `uv run basedpyright` 必须保持 `0 errors, 0 warnings`。
- 新增工具至少做一次本地 fake bot 烟测，确认：
  - 工具 schema 能正确暴露给 AI。
  - 正常调用返回结构化结果。
  - 可恢复错误能作为工具结果返回给 AI。
