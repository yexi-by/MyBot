"""完整工具目录、实时权限和聚合查询授权的行为回归。"""

from typing import Literal

import pytest
from pydantic import Field

from qq_agent_harness.contracts import (
    ConversationScope,
    JsonObject,
    JsonValue,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    schema,
)
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.settings import (
    ConversationSettings,
    HarnessSettings,
    ParameterRule,
    Policy,
)


class Empty(Model):
    pass


class UserInput(Model):
    user_id: str = Field(min_length=1)


class Connection:
    async def call(
        self, action: str, params: JsonObject, *, timeout: float | None = None
    ) -> JsonValue:
        raise AssertionError("注册表测试不应发起网络请求")


async def echo(context: ToolContext, arguments: JsonObject) -> ToolResult:
    return ToolResult.success({"scope": context.target.key, "arguments": arguments})


def setup_registry() -> tuple[ToolRegistry, ToolContext, Policy]:
    policy = Policy(categories={"fixture": True})
    settings = HarnessSettings(
        groups={"10": ConversationSettings(enabled=True, policy=policy)},
        private=ConversationSettings(enabled=True, policy=policy),
    )
    registry = ToolRegistry(lambda: settings)
    context = ToolContext(
        ConversationScope(bot_id="1", kind="group", peer_id="10"), "test-turn", Connection()
    )
    return registry, context, settings.groups["10"].policy


async def test_all_authorized_tools_are_immediately_visible_in_stable_order() -> None:
    registry, context, policy = setup_registry()
    for index in reversed(range(45)):
        registry.add(
            ToolDefinition(
                f"fixture__group_{index:02}",
                "当前群查询",
                "fixture",
                schema(Empty),
                echo,
                title="群信息",
            )
        )
    registry.add(
        ToolDefinition(
            "fixture__hidden_avatar",
            "群主头像",
            "fixture",
            schema(Empty),
            echo,
        )
    )
    policy.tools["fixture__hidden_avatar"] = False

    expected = [f"fixture__group_{index:02}" for index in range(45)]
    assert [item["name"] for item in registry.definitions(context)] == expected
    assert registry.available(context.scope) == expected
    assert (await registry.execute(context, expected[-1], {})).status == "ok"


async def test_definitions_follow_live_permissions_and_catalog_changes() -> None:
    registry, context, policy = setup_registry()
    for name in ("qq__read_media", "qq__get_history", "qq__get_message", "qq__send_message"):
        registry.add(ToolDefinition(name, "基础能力", "fixture", schema(Empty), echo))
    policy.tools["qq__read_media"] = False
    definitions = {item["name"]: item for item in registry.definitions(context)}
    assert "qq__get_history" in definitions and "qq__get_message" in definitions
    assert "qq__read_media" not in definitions
    registry.add(ToolDefinition("fixture__added", "新增能力", "fixture", schema(Empty), echo))
    assert "fixture__added" in [item["name"] for item in registry.definitions(context)]
    registry.remove_prefix("fixture__")
    assert "fixture__added" not in [item["name"] for item in registry.definitions(context)]
    assert (await registry.execute(context, "fixture__added", {})).error_type == "ToolNotFound"
    policy.categories["fixture"] = False
    assert (await registry.execute(context, "qq__get_history", {})).error_type == "PermissionDenied"
    assert registry.definitions(context) == []
    policy.tools["qq__read_media"] = True
    assert [item["name"] for item in registry.definitions(context)] == ["qq__read_media"]


@pytest.mark.parametrize(
    ("scope", "cross_groups", "cross_private", "expected"),
    [
        ("group", [], False, set()),
        ("group", ["20"], True, {"target_group_id"}),
        ("private", ["20"], True, {"target_user_id"}),
        ("both", ["20"], False, {"target_group_id"}),
        ("both", [], True, {"target_user_id"}),
        ("account", ["20"], True, set()),
    ],
)
def test_target_schema_matches_policy_and_tool_scope(
    scope: Literal["group", "private", "both", "account"],
    cross_groups: list[str],
    cross_private: bool,
    expected: set[str],
) -> None:
    registry, context, policy = setup_registry()
    policy.cross_groups = cross_groups
    policy.cross_private = cross_private
    tool = ToolDefinition(
        "fixture__lookup", "资料", "fixture", schema(UserInput), echo, scope=scope, cross_scope=True
    )
    registry.add(tool)
    definition = registry.definition(tool, context.scope)
    parameters = definition["parameters"]
    assert isinstance(parameters, dict) and isinstance(parameters["properties"], dict)
    assert set(parameters["properties"]) == {"user_id"} | expected
    assert "target_group_id" not in str(tool.parameters)


async def test_direct_call_returns_schema_without_input_and_observes_revocation() -> None:
    registry, context, policy = setup_registry()
    for name in ("fixture__a", "fixture__b"):
        registry.add(
            ToolDefinition(
                name,
                "用户资料",
                "fixture",
                schema(UserInput),
                echo,
                model=UserInput,
                cross_scope=True,
            )
        )
    invalid = await registry.execute(context, "fixture__a", {"unknown": "SECRET_SENTINEL"})
    assert invalid.error_type == "InvalidArguments"
    assert isinstance(invalid.data, dict) and isinstance(invalid.data["schema"], dict)
    assert "SECRET_SENTINEL" not in invalid.model_dump_json()
    assert "target_group_id" not in str(invalid.data)
    for name in ("fixture__a", "fixture__b", "fixture__a"):
        result = await registry.execute(context, name, {"user_id": "30"})
        assert result.status == "ok"
    policy.tools["fixture__a"] = False
    denied = await registry.execute(context, "fixture__a", {"user_id": "30"})
    assert denied.error_type == "PermissionDenied"
    assert "schema" not in str(denied.data)


async def test_aggregate_check_enforces_child_parameters_parent_revocation_and_cancel() -> None:
    registry, context, policy = setup_registry()
    reached: list[str] = []
    cancel = False
    context.cancelled = lambda: cancel

    async def aggregate(ctx: ToolContext, arguments: JsonObject) -> ToolResult:
        denied = ctx.check_tool("fixture__child", arguments)
        if denied:
            return denied
        reached.append(str(arguments["user_id"]))
        policy.tools["fixture__parent"] = False
        return ctx.check_tool("fixture__child", arguments) or ToolResult.success()

    registry.add(
        ToolDefinition(
            "fixture__parent", "复合查询", "fixture", schema(UserInput), aggregate, model=UserInput
        )
    )
    registry.add(
        ToolDefinition(
            "fixture__child", "子查询", "fixture", schema(UserInput), echo, model=UserInput
        )
    )
    policy.parameters["fixture__child"] = {"user_id": ParameterRule(allowed=["30"])}
    denied = await registry.execute(context, "fixture__parent", {"user_id": "40"})
    assert denied.error_type == "PermissionDenied" and not reached
    revoked = await registry.execute(context, "fixture__parent", {"user_id": "30"})
    assert revoked.error_type == "PermissionDenied" and reached == ["30"]
    assert context.check_tool("fixture__child", {"user_id": "30"}) is not None
    policy.tools["fixture__parent"] = True
    cancel = True
    assert (
        await registry.execute(context, "fixture__parent", {"user_id": "30"})
    ).status == "cancelled"
    assert reached == ["30"]
