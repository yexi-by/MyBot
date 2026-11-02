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
    compact_schema,
)
from .settings import HarnessSettings, Policy

_SEARCH_TOOL_NAME = "qq__search_tools"
_SEARCH_TOOL_DEFINITION: JsonObject = {
    "name": _SEARCH_TOOL_NAME,
    "description": (
        "搜索并加载未默认展示的 QQ 扩展工具（如群管禁言、踢人、头衔名片、发公告、群待办、"
        "群相册、群文件管理、加群审批、AI 语音、收藏表情、修改资料或头像等）。"
        "可传入关键词或分类名称查找对应工具并获取完整参数定义；留空则列出当前会话可用的全部扩展工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（如“禁言”“公告”“头衔”“相册”“文件”）；留空列出全部可用扩展工具",
            },
            "category": {
                "type": "string",
                "description": "按分类筛选（可选，如 members、groups、notices、files、albums、contacts、account、utilities、system）",
            },
        },
        "additionalProperties": False,
    },
}


def _match_score(haystack: str, raw_query: str, terms: list[str]) -> int:
    score = 0
    if raw_query in haystack:
        score += 100
    for term in terms:
        if term in haystack:
            score += 25
        elif len(term) >= 3:
            for index in range(len(term) - 1):
                piece = term[index : index + 2]
                if not piece.isascii() and piece in haystack:
                    score += 5
    return score


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
        if tool.name.startswith("image__"):
            image = self.settings().image
            if not image.enabled:
                return False, "尚未启用图像生成"
            if not image.base_url.strip() or not image.model.strip():
                return False, "图像生成尚未配置接口地址或模型名称"
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
        result: list[JsonObject] = []
        has_deferred = False
        for name in sorted(self.tools):
            tool = self.tools[name]
            if not self.permitted(context.scope, tool)[0]:
                continue
            if tool.deferred:
                has_deferred = True
                if name not in context.discovered_tools:
                    continue
            result.append(self.definition(tool, context.scope))
        if has_deferred and _SEARCH_TOOL_NAME not in self.tools:
            result.append(copy.deepcopy(_SEARCH_TOOL_DEFINITION))
        return result

    def _search_tools(self, context: ToolContext, arguments: JsonObject) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="本轮已停止")
        unknown_keys = set(arguments) - {"query", "category"}
        raw_query = arguments.get("query", "")
        raw_category = arguments.get("category", "")
        if (
            unknown_keys
            or (raw_query is not None and not isinstance(raw_query, str))
            or (raw_category is not None and not isinstance(raw_category, str))
        ):
            return ToolResult(
                status="error",
                error_type="InvalidArguments",
                message="参数格式无效，仅支持字符串类型的 query 和 category",
                data={"tool": _SEARCH_TOOL_NAME, "schema": _SEARCH_TOOL_DEFINITION["parameters"]},
            )
        query = (raw_query or "").strip().casefold()
        category = (raw_category or "").strip().casefold()
        terms = [
            part
            for part in query.replace("，", " ").replace(",", " ").replace("、", " ").split()
            if part
        ]
        permitted_tools = [
            self.tools[name]
            for name in sorted(self.tools)
            if self.permitted(context.scope, self.tools[name])[0]
        ]
        deferred_tools = [tool for tool in permitted_tools if tool.deferred]
        if not query and not category:
            if len(deferred_tools) <= 15:
                for tool in deferred_tools:
                    context.discovered_tools.add(tool.name)
                return ToolResult.success(
                    {
                        "matched": [
                            {
                                **self.definition(tool, context.scope),
                                "title": tool.title or tool.name,
                                "category": tool.category,
                            }
                            for tool in deferred_tools
                        ],
                        "total_matched": len(deferred_tools),
                    }
                )
            return ToolResult.success(
                {
                    "available_tools": [
                        {
                            "name": tool.name,
                            "title": tool.title or tool.name,
                            "category": tool.category,
                            "description": tool.description,
                        }
                        for tool in deferred_tools
                    ],
                    "total_available": len(deferred_tools),
                }
            )
        scored: list[tuple[int, ToolDefinition]] = []
        for tool in permitted_tools:
            cat_label = self.category_label(tool.category).casefold()
            if category and category not in tool.category.casefold() and category not in cat_label:
                continue
            if not terms:
                scored.append((10, tool))
                continue
            haystack = (
                f"{tool.name} {tool.title} {tool.description} {tool.category} {cat_label}"
            ).casefold()
            score = _match_score(haystack, query, terms)
            if score > 0:
                scored.append((score, tool))
        scored.sort(key=lambda item: (-item[0], not item[1].deferred, item[1].name))
        matched = [tool for _, tool in scored[:15]]
        for tool in matched:
            if tool.deferred:
                context.discovered_tools.add(tool.name)
        payload: JsonObject = {
            "matched": [
                {
                    **self.definition(tool, context.scope),
                    "title": tool.title or tool.name,
                    "category": tool.category,
                }
                for tool in matched
            ],
            "total_matched": len(scored),
        }
        if not matched:
            payload["available_tools"] = [
                {
                    "name": tool.name,
                    "title": tool.title or tool.name,
                    "category": tool.category,
                }
                for tool in deferred_tools
            ]
        return ToolResult.success(payload)

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
        if name == _SEARCH_TOOL_NAME and name not in self.tools:
            return self._search_tools(context, arguments)
        tool = self.tools.get(name)
        if not tool:
            return ToolResult.failure("工具不存在，请使用当前提供的工具", "ToolNotFound")
        permitted, reason = self.permitted(context.scope, tool)
        if not permitted:
            return ToolResult.failure(reason, "PermissionDenied")
        if context.cancelled():
            return ToolResult(status="cancelled", message="本轮已停止")
        if tool.deferred:
            context.discovered_tools.add(name)
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
                return ToolResult.failure("子查询所需工具未开放", "PermissionDenied")
            child_args = copy.deepcopy(child_arguments)
            try:
                child_args = child.binding.prepare(child_args).arguments
            except SchemaError, ValueError:
                return ToolResult.failure("子查询参数无效", "InvalidArguments")
            return self.authorize(context.scope, child, child_args, target)

        context.target_scope = target
        context.recheck = recheck
        previous_check_tool = context.check_tool
        context.check_tool = check_tool
        try:
            result = await prepared.invoke(context)
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            result = ToolResult.failure(str(exc), "InvalidArguments")
        except TimeoutError, ConnectionError, OSError:
            result = ToolResult(
                status="error",
                message="工具连接中断或请求超时",
                error_type="TransportError",
            )
        finally:
            context.target_scope = None
            context.recheck = lambda: None
            context.check_tool = previous_check_tool
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
