"""按群授权的完整工具定义与执行共用同一目录。"""

import asyncio
import copy
from collections.abc import Callable, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from pydantic import ValidationError

from .contracts import (
    ConversationScope,
    JsonObject,
    JsonValue,
    ToolContext,
    ToolDefinition,
    ToolResult,
    UnknownOperation,
    compact_schema,
)
from .settings import HarnessSettings, Policy


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

    def definitions(self, context: ToolContext) -> list[JsonObject]:
        return [
            self.definition(self.tools[name], context.scope)
            for name in sorted(self.tools)
            if self.permitted(context.scope, self.tools[name])[0]
        ]

    def definition(self, tool: ToolDefinition, scope: ConversationScope) -> JsonObject:
        parameters = compact_schema(tool.parameters)
        if tool.cross_scope:
            properties = parameters.get("properties")
            if isinstance(properties, dict):
                policy = self.policy(scope)
                if tool.scope in ("both", "group") and policy.cross_groups:
                    properties["target_group_id"] = {
                        "type": "string",
                        "enum": [name for name in sorted(policy.cross_groups)],
                        "description": "跨群目标；省略表示当前会话",
                    }
                if tool.scope in ("both", "private") and policy.cross_private:
                    properties["target_user_id"] = {
                        "type": "string",
                        "minLength": 1,
                        "description": "跨私聊目标 QQ 号；省略表示当前会话",
                    }
        return {"name": tool.name, "description": tool.description, "parameters": parameters}

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
        if name == "qq__send_message" and has_at_all(args) and not policy.allow_at_all:
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

    def invalid_arguments(
        self,
        scope: ConversationScope,
        tool: ToolDefinition,
        message: str,
        problems: JsonValue = None,
    ) -> ToolResult:
        return ToolResult(
            status="error",
            error_type="InvalidArguments",
            message=message,
            data={
                "tool": tool.name,
                "schema": self.definition(tool, scope)["parameters"],
                "problems": problems,
            },
        )

    async def execute(self, context: ToolContext, name: str, arguments: JsonObject) -> ToolResult:
        tool = self.tools.get(name)
        if not tool:
            return ToolResult.failure("工具不存在，请使用当前提供的工具", "ToolNotFound")
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
            prepared = tool.binding.prepare(args)
            args = prepared.arguments
        except SchemaError as error:
            path = ".".join(str(part) for part in error.absolute_schema_path)
            return self.invalid_arguments(
                context.scope, tool, f"参数不符合 schema；校验位置：{path}"
            )
        except ValidationError as error:
            problems: list[JsonValue] = [
                {"field": [str(part) for part in item["loc"]], "type": item["type"]}
                for item in error.errors(include_input=False, include_url=False)
            ]
            return self.invalid_arguments(
                context.scope, tool, "参数格式无效，请按字段错误修正", problems
            )
        except ValueError:
            return self.invalid_arguments(
                context.scope, tool, "参数不符合工具 schema，请检查类型、必填字段和取值范围"
            )
        denied = self.authorize(context.scope, tool, args, target)
        if denied:
            return denied
        if tool.effects and any(
            operation.tool == name and operation.arguments == args and operation.target == target
            for operation in context.unknown_effects
        ):
            return ToolResult(
                status="unknown", message="此前相同操作结果未知；请在控制台核实并解除该记录后重试"
            )

        def recheck() -> ToolResult | None:
            if context.cancelled():
                return ToolResult(status="cancelled", message="本轮已停止")
            context.max_sends = self.policy(context.scope).max_sends_per_turn
            denied = self.authorize(context.scope, tool, args, target)
            if denied:
                return denied
            if name == "qq__send_message" and context.send_attempts >= context.max_sends:
                return ToolResult.failure("已达到本轮实际发送上限", "SendLimit")
            return None

        def check_tool(child_name: str, child_arguments: JsonObject) -> ToolResult | None:
            denied = recheck()
            if denied:
                return denied
            child = self.tools.get(child_name)
            if child is None or not self.permitted(context.scope, child)[0]:
                return ToolResult.failure("聚合查询所需能力未获授权", "PermissionDenied")
            child_args = copy.deepcopy(child_arguments)
            try:
                child_args = child.binding.prepare(child_args).arguments
            except SchemaError, ValueError:
                return ToolResult.failure("聚合查询参数无效", "InvalidArguments")
            return self.authorize(context.scope, child, child_args, target)

        context.target_scope = target
        context.recheck = recheck
        previous_check_tool = context.check_tool
        context.check_tool = check_tool
        context.effect_submitted = False
        try:
            result = await prepared.invoke(context)
        except asyncio.CancelledError:
            if tool.effects and context.effect_submitted:
                # 取消等待不能证明外部动作未生效；随会话保留，下一次唤醒也不得盲目重试。
                context.unknown_effects.append(UnknownOperation(name, args, target))
            raise
        except ValueError as exc:
            result = ToolResult.failure(str(exc), "InvalidArguments")
        except TimeoutError, ConnectionError, OSError:
            result = ToolResult(
                status="unknown" if tool.effects and context.effect_submitted else "error",
                message="工具连接中断或超时；有副作用的操作可能已经生效",
                error_type="TransportError",
            )
        finally:
            context.target_scope = None
            context.recheck = lambda: None
            context.check_tool = previous_check_tool
        if result.status == "unknown" and tool.effects:
            context.unknown_effects.append(UnknownOperation(name, args, target))
        return result

    def catalog(self, scope: ConversationScope) -> JsonObject:
        tools: list[JsonValue] = []
        for tool in self.tools.values():
            allowed, reason = self.permitted(scope, tool)
            item = tool.public()
            item.update(allowed=allowed, reason=reason)
            tools.append(item)
        present = {t.category for t in self.tools.values()}
        known = [name for name in self.category_labels if name in present]
        ordered = known + sorted(present - set(known))
        return {
            "tools": tools,
            "categories": [{"id": name, "label": self.category_label(name)} for name in ordered],
        }

    def available(self, scope: ConversationScope) -> list[str]:
        """当前配置下该会话可调用的全部工具，按名称稳定排序。"""
        return [name for name, tool in sorted(self.tools.items()) if self.permitted(scope, tool)[0]]
