"""按群授权、按需披露与工具执行共用同一目录。"""

import asyncio
import copy
from collections.abc import Callable, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from pydantic import Field, ValidationError

from .contracts import (
    JSON_OBJECT,
    ConversationScope,
    JsonObject,
    JsonValue,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    schema,
)
from .settings import HarnessSettings, Policy


class DiscoverInput(Model):
    query: str = Field(default="", description="需要的能力或关键词；空字符串列出当前可用工具")
    category: str = Field(default="", description="可选能力分类")
    offset: int = Field(
        default=0,
        ge=0,
        le=10_000,
        description="分页起点；结果有 next_offset 时继续查询，避免遗漏后面的能力",
    )


class LoadInput(Model):
    names: list[str] = Field(
        min_length=1, max_length=12, description="需要加载完整参数定义的工具名称"
    )


def values_at(value: JsonValue, parts: list[str]) -> list[JsonValue]:
    if not parts:
        return [value]
    head, *tail = parts
    if head == "*" and isinstance(value, (list, dict)):
        items = value if isinstance(value, list) else value.values()
        return [result for item in items for result in values_at(item, tail)]
    if isinstance(value, dict) and head in value:
        return values_at(value[head], tail)
    if isinstance(value, list) and head.isdigit() and int(head) < len(value):
        return values_at(value[int(head)], tail)
    return []


def has_at_all(value: JsonValue) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "at" or value.get("kind") == "at":
            data = value.get("data", value)
            if isinstance(data, dict) and str(data.get("qq", data.get("user_id", ""))) == "all":
                return True
        return any(has_at_all(item) for item in value.values())
    return isinstance(value, list) and any(has_at_all(item) for item in value)


class ToolRegistry:
    def __init__(self, settings: Callable[[], HarnessSettings]) -> None:
        self.settings = settings
        self.tools: dict[str, ToolDefinition] = {}
        # 分类 ID 到控制台名称；插入顺序即展示顺序。
        self.category_labels: dict[str, str] = {}
        self.add(
            ToolDefinition(
                "harness__discover_tools",
                "当你需要查找可辅助当前对话的能力时，主动搜索工具；也可在缺少资料、需要计算或处理 QQ 操作时发现相关能力。",
                "harness",
                schema(DiscoverInput),
                self.discover,
                model=DiscoverInput,
                title="发现工具",
            )
        )
        self.add(
            ToolDefinition(
                "harness__load_tools",
                "当你已选定需要使用的工具时，加载其完整参数定义；随后以实际工具名称调用，不在普通正文里描述为已经执行。",
                "harness",
                schema(LoadInput),
                self.load,
                model=LoadInput,
                title="加载工具",
            )
        )

    def add(self, tool: ToolDefinition) -> None:
        if tool.name in self.tools:
            raise ValueError(f"工具名称重复：{tool.name}")
        Draft202012Validator.check_schema(tool.parameters)
        self.tools[tool.name] = tool

    def describe_categories(self, labels: Mapping[str, str]) -> None:
        self.category_labels.update(labels)

    def category_label(self, category: str) -> str:
        if category in self.category_labels:
            return self.category_labels[category]
        if category.startswith("mcp."):
            return "MCP · " + category.removeprefix("mcp.")
        return category

    def remove_prefix(self, prefix: str) -> None:
        for name in list(self.tools):
            if name.startswith(prefix):
                del self.tools[name]

    def policy(self, scope: ConversationScope) -> Policy:
        return self.settings().conversation(scope).policy

    def permitted(self, scope: ConversationScope, tool: ToolDefinition) -> tuple[bool, str]:
        conversation = self.settings().conversation(scope)
        if not conversation.enabled:
            return False, "当前会话未启用"
        if tool.category == "harness":
            return True, ""
        if tool.name.startswith("sandbox__") and not self.settings().sandbox.enabled:
            return False, "此部署尚未启用共享沙箱"
        if tool.scope not in ("both", "account", scope.kind):
            return False, "工具不适用于当前会话类型"
        policy = conversation.policy
        allowed = policy.tools.get(tool.name, policy.categories.get(tool.category, False))
        for dependency in tool.requires:
            prerequisite = self.tools.get(dependency)
            if prerequisite is None or not self.permitted(scope, prerequisite)[0]:
                return False, f"还需开放 {dependency}"
        return allowed, "" if allowed else "当前会话未开放此能力"

    def initial(self, context: ToolContext) -> None:
        context.loaded_tools.update({"harness__discover_tools", "harness__load_tools"})
        send = self.tools.get("qq__send_message")
        if send and self.permitted(context.scope, send)[0]:
            context.loaded_tools.add(send.name)

    def definitions(self, context: ToolContext) -> list[JsonObject]:
        return [
            self.definition(tool)
            for name in sorted(context.loaded_tools)
            if (tool := self.tools.get(name)) and self.permitted(context.scope, tool)[0]
        ]

    @staticmethod
    def definition(tool: ToolDefinition) -> JsonObject:
        parameters = copy.deepcopy(tool.parameters)
        if tool.cross_scope:
            properties = parameters.get("properties")
            if isinstance(properties, dict):
                properties["target_group_id"] = {
                    "type": "string",
                    "description": "仅在当前群策略明确允许跨群时指定目标群；省略表示当前会话",
                }
                properties["target_user_id"] = {
                    "type": "string",
                    "description": "仅在当前会话策略允许跨私聊时指定目标；省略表示当前会话",
                }
        return {"name": tool.name, "description": tool.description, "parameters": parameters}

    async def discover(self, context: ToolContext, arguments: JsonObject) -> ToolResult:
        query = str(arguments.get("query", "")).casefold()
        category = arguments.get("category", "")
        allowed = [
            tool
            for tool in self.tools.values()
            if self.permitted(context.scope, tool)[0] and tool.category != "harness"
        ]
        selected = [
            tool
            for tool in allowed
            if (not category or category == tool.category)
            and (
                not query
                or any(
                    word in (tool.name + tool.description + tool.category).casefold()
                    for word in query.split()
                )
            )
        ]
        offset = DiscoverInput.model_validate(arguments).offset
        page = selected[offset : offset + 40]
        return ToolResult.success(
            {
                "tools": [
                    {"name": t.name, "description": t.description, "category": t.category}
                    for t in page
                ],
                "categories": [category for category in sorted({t.category for t in allowed})],
                "total": len(selected),
                "next_offset": offset + 40 if offset + 40 < len(selected) else None,
            }
        )

    async def load(self, context: ToolContext, arguments: JsonObject) -> ToolResult:
        parsed = LoadInput.model_validate(arguments)
        definitions: list[JsonValue] = []
        for name in parsed.names:
            tool = self.tools.get(name)
            if not tool or not self.permitted(context.scope, tool)[0]:
                return ToolResult.failure("工具不存在或未获当前会话授权", "PermissionDenied")
            definitions.append(self.definition(tool))
        context.loaded_tools.update(parsed.names)
        return ToolResult.success(definitions)

    def authorize(
        self,
        scope: ConversationScope,
        tool: ToolDefinition,
        args: JsonObject,
        target: ConversationScope | None = None,
    ) -> ToolResult | None:
        permitted, reason = self.permitted(scope, tool)
        if not permitted or tool.name not in self.tools:
            return ToolResult.failure(reason or "工具已经移除", "PermissionDenied")
        target = target or scope
        policy = self.policy(scope)
        if target != scope:
            if target.kind == "group" and target.peer_id not in policy.cross_groups:
                return ToolResult.failure("当前策略未允许此跨群目标", "PermissionDenied")
            if target.kind == "private" and not policy.cross_private:
                return ToolResult.failure("当前策略未允许跨私聊", "PermissionDenied")
        if tool.scope not in ("both", "account", target.kind):
            return ToolResult.failure("工具不适用于目标会话类型", "PermissionDenied")
        name = tool.name
        if has_at_all(args) and not policy.allow_at_all:
            return ToolResult.failure("当前群未允许 @全体", "PermissionDenied")
        for path, rule in policy.parameters.get(name, {}).items():
            for value in values_at(args, path.split(".")):
                if rule.allowed is not None and value not in rule.allowed:
                    return ToolResult.failure(f"参数 {path} 不在允许范围", "PermissionDenied")
                if rule.minimum is not None and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or value < rule.minimum
                ):
                    return ToolResult.failure(
                        f"参数 {path} 不能小于 {rule.minimum}", "PermissionDenied"
                    )
                if rule.maximum is not None and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or value > rule.maximum
                ):
                    return ToolResult.failure(
                        f"参数 {path} 不能大于 {rule.maximum}", "PermissionDenied"
                    )
                if rule.max_length is not None and (
                    not isinstance(value, (str, list, dict)) or len(value) > rule.max_length
                ):
                    return ToolResult.failure(
                        f"参数 {path} 长度不能超过 {rule.max_length}", "PermissionDenied"
                    )
        return None

    async def execute(self, context: ToolContext, name: str, arguments: JsonObject) -> ToolResult:
        tool = self.tools.get(name)
        if not tool or name not in context.loaded_tools:
            return ToolResult.failure("工具尚未加载或不存在，请先发现并加载工具", "ToolNotLoaded")
        permitted, reason = self.permitted(context.scope, tool)
        if not permitted:
            return ToolResult.failure(reason, "PermissionDenied")
        if context.cancelled():
            return ToolResult(status="cancelled", message="本轮已停止")
        policy = self.policy(context.scope)
        context.max_sends = policy.max_sends_per_turn
        args = copy.deepcopy(arguments)
        target = context.scope
        if tool.cross_scope:
            group = args.pop("target_group_id", None)
            user = args.pop("target_user_id", None)
            if group and user:
                return ToolResult.failure("不能同时指定群和私聊目标")
            if group:
                if (
                    not isinstance(group, str)
                    or (group != context.scope.peer_id or context.scope.kind != "group")
                    and group not in policy.cross_groups
                ):
                    return ToolResult.failure("当前群策略未允许此跨群目标", "PermissionDenied")
                target = ConversationScope(bot_id=context.scope.bot_id, kind="group", peer_id=group)
            if user:
                if (
                    not isinstance(user, str)
                    or (user != context.scope.peer_id or context.scope.kind != "private")
                    and not policy.cross_private
                ):
                    return ToolResult.failure("当前会话策略未允许跨私聊", "PermissionDenied")
                target = ConversationScope(
                    bot_id=context.scope.bot_id, kind="private", peer_id=user
                )
        if tool.scope not in ("both", "account", target.kind):
            return ToolResult.failure("工具不适用于指定的目标会话类型", "PermissionDenied")
        try:
            Draft202012Validator(tool.parameters).validate(args)
            if tool.model:
                args = JSON_OBJECT.validate_python(
                    tool.model.model_validate(args).model_dump(mode="json")
                )
        except SchemaError, ValidationError, ValueError:
            return ToolResult.failure(
                "参数不符合工具 schema，请检查类型、必填字段和取值范围", "InvalidArguments"
            )
        denied = self.authorize(context.scope, tool, args, target)
        if denied:
            return denied
        if name == "qq__send_message":
            if context.send_attempts >= policy.max_sends_per_turn:
                return ToolResult.failure("已达到本轮发送次数限制", "SendLimit")
            context.send_attempts += 1
        if tool.effects and any(
            previous_name == name and previous_args == args and previous_target == target
            for previous_name, previous_args, previous_target in context.unknown_effects
        ):
            return ToolResult(status="unknown", message="此前相同操作结果未知，不能直接重复执行")

        def recheck() -> ToolResult | None:
            if context.cancelled():
                return ToolResult(status="cancelled", message="本轮已停止")
            context.max_sends = self.policy(context.scope).max_sends_per_turn
            denied = self.authorize(context.scope, tool, args, target)
            if denied:
                return denied
            if name == "qq__send_message" and len(context.sent_messages) >= context.max_sends:
                return ToolResult.failure("已达到本轮实际发送上限", "SendLimit")
            return None

        context.target_scope = target
        context.recheck = recheck
        try:
            result = await tool.handler(context, args)
        except asyncio.CancelledError:
            if tool.effects:
                # 取消等待不能证明外部动作未生效；随会话保留，下一次唤醒也不得盲目重试。
                context.unknown_effects.append((name, args, target))
            raise
        except ValueError as exc:
            result = ToolResult.failure(str(exc), "InvalidArguments")
        except TimeoutError, ConnectionError, OSError:
            result = ToolResult(
                status="unknown" if tool.effects else "error",
                message="工具连接中断或超时；有副作用的操作可能已经生效",
                error_type="TransportError",
            )
        finally:
            context.target_scope = None
            context.recheck = lambda: None
        if result.status == "unknown" and tool.effects:
            context.unknown_effects.append((name, args, target))
        return result

    def catalog(self, scope: ConversationScope) -> JsonObject:
        tools: list[JsonValue] = []
        for tool in self.tools.values():
            allowed, reason = self.permitted(scope, tool)
            item = tool.public()
            item.update(allowed=allowed, reason=reason)
            tools.append(item)
        present = {t.category for t in self.tools.values() if t.category != "harness"}
        known = [name for name in self.category_labels if name in present]
        ordered = known + sorted(present - set(known))
        return {
            "tools": tools,
            "categories": [{"id": name, "label": self.category_label(name)} for name in ordered],
        }

    def available(self, scope: ConversationScope) -> list[str]:
        """当前配置下该会话可调用的业务工具；内置的发现与加载不计入。"""
        return [
            name
            for name, tool in self.tools.items()
            if tool.category != "harness" and self.permitted(scope, tool)[0]
        ]
