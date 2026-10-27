"""验证成员检索的有界输出、群资料清洗和复合查询授权边界。"""

import pytest
from test_napcat import Bot, registry

from qq_agent_harness.contracts import JsonObject, JsonValue
from qq_agent_harness.settings import HarnessSettings, ParameterRule


async def test_member_search_filters_before_paging_and_omits_protocol_noise() -> None:
    members: list[JsonValue] = [
        {
            "user_id": index + 1000,
            "nickname": "Alice" if index % 2 == 0 else "Bob",
            "card": f"成员 {index}",
            "title": "维护者" if index == 4 else "",
            "role": "owner" if index == 4 else "member",
            "unneeded_flags": "x" * 10000,
        }
        for index in range(1200)
    ]

    async def remote(action: str, params: JsonObject) -> JsonValue:
        assert action == "get_group_member_list"
        assert params == {"group_id": "10", "no_cache": False}
        return members

    tools, context, _ = registry(Bot(remote))
    result = await tools.execute(
        context, "qq__get_group_member_list", {"query": "ALICE", "limit": 2, "offset": 1}
    )
    assert result.status == "ok" and isinstance(result.data, dict)
    assert result.data["total"] == 1200 and result.data["matched"] == 600
    assert result.data["next_offset"] == 3
    page = result.data["items"]
    assert isinstance(page, list)
    assert [item["user_id"] for item in page if isinstance(item, dict)] == ["1002", "1004"]
    assert "unneeded_flags" not in str(result.data)
    assert len(str(result.data)) < 1000
    owner = await tools.execute(context, "qq__get_group_member_list", {"role": "owner"})
    assert isinstance(owner.data, dict) and owner.data["matched"] == 1
    assert owner.data["next_offset"] is None
    for query in ("成员 4", "1004", "维护者"):
        found = await tools.execute(
            context, "qq__get_group_member_list", {"role": "owner", "query": query}
        )
        assert isinstance(found.data, dict) and found.data["matched"] == 1
    default_page = await tools.execute(context, "qq__get_group_member_list", {})
    assert isinstance(default_page.data, dict)
    assert isinstance(default_page.data["items"], list) and len(default_page.data["items"]) == 50


@pytest.mark.parametrize("owner_value", [0, "0", None])
async def test_group_owner_fallback_cleans_data_and_uses_authorized_target(owner_value) -> None:
    async def remote(action: str, params: JsonObject) -> JsonValue:
        assert params["group_id"] == "99"
        if action == "get_group_detail_info":
            return {
                "ownerUin": owner_value,
                "groupName": "资料群",
                "groupMemo": "群介绍",
                "memberNum": 42,
                "maxMemberNum": 200,
                "groupQuestion": "回答问题",
                "protocolFlags": "internal",
            }
        assert action == "get_group_member_list"
        return [{"user_id": 123, "role": "owner"}, {"user_id": 456, "role": "member"}]

    tools, context, settings = registry(Bot(remote))
    settings.groups["10"].policy.cross_groups = ["99"]
    response = await tools.execute(context, "qq__get_group_detail_info", {"target_group_id": "99"})
    assert response.status == "ok" and isinstance(response.data, dict)
    assert response.data == {
        "group_id": "99",
        "owner_id": "123",
        "group_name": "资料群",
        "group_memo": "群介绍",
        "member_count": 42,
        "max_member_count": 200,
        "group_question": "回答问题",
        "avatar": {"tool": "qq__read_media", "group_avatar": True, "target_group_id": "99"},
    }


async def test_owner_lookup_does_not_bypass_member_permission() -> None:
    async def remote(action: str, _: JsonObject) -> JsonValue:
        assert action == "get_group_info"
        return {"group_name": "资料群", "owner_id": 0}

    bot = Bot(remote)
    tools, context, settings = registry(bot)
    settings.groups["10"].policy.tools["qq__get_group_member_list"] = False
    response = await tools.execute(context, "qq__get_group_info", {})
    assert response.status == "ok" and isinstance(response.data, dict)
    assert response.data["owner_id"] is None
    assert response.data["owner_lookup"] == {"status": "error", "error_type": "PermissionDenied"}
    assert len(bot.calls) == 1


async def test_member_profile_aggregation_checks_each_capability_and_parameter_policy() -> None:
    async def remote(action: str, params: JsonObject) -> JsonValue:
        if action == "get_group_member_info":
            assert params == {"user_id": "123", "no_cache": True, "group_id": "10"}
            return {"user_id": "123", "card": "名片", "role": "admin", "internal": "noise"}
        assert action == "get_stranger_info"
        assert params == {"user_id": "123", "no_cache": True}
        return {"nickname": "名字", "longNick": "签名", "qqLevel": 20}

    bot = Bot(remote)
    tools, context, settings = registry(bot)
    policy = settings.groups["10"].policy
    policy.tools["qq__nc_get_user_status"] = False
    request: JsonObject = {"user_id": "123", "no_cache": True, "include_status": True}
    response = await tools.execute(context, "qq__get_group_member_info", request)
    assert response.status == "ok" and isinstance(response.data, dict)
    assert response.data == {
        "user_id": "123",
        "card": "名片",
        "role": "admin",
        "avatar": {"tool": "qq__read_media", "user_id": "123"},
        "profile": {"nickname": "名字", "longNick": "签名", "qqLevel": 20},
        "status_error": {"status": "error", "error_type": "PermissionDenied"},
    }
    policy.parameters["qq__get_stranger_info"] = {"user_id": ParameterRule(allowed=["999"])}
    denied = await tools.execute(context, "qq__get_group_member_info", request)
    assert denied.status == "ok" and isinstance(denied.data, dict)
    assert "profile" not in denied.data
    assert denied.data["profile_error"] == {"status": "error", "error_type": "PermissionDenied"}
    assert [action for action, _ in bot.calls] == [
        "get_group_member_info",
        "get_stranger_info",
        "get_group_member_info",
    ]


@pytest.mark.parametrize("stop", ["cancel", "revoke"])
async def test_member_query_rechecks_before_each_extra_read(stop: str) -> None:
    settings: HarnessSettings
    cancelled = False

    async def remote(action: str, _: JsonObject) -> JsonValue:
        nonlocal cancelled
        if action == "get_group_member_info":
            return {"user_id": "123", "role": "member"}
        assert action == "get_stranger_info"
        if stop == "cancel":
            cancelled = True
        else:
            settings.groups["10"].policy.tools["qq__get_group_member_info"] = False
        return {"nickname": "名字"}

    bot = Bot(remote)
    tools, context, settings = registry(bot)
    context.cancelled = lambda: cancelled
    response = await tools.execute(
        context, "qq__get_group_member_info", {"user_id": "123", "include_status": True}
    )
    assert [action for action, _ in bot.calls] == ["get_group_member_info", "get_stranger_info"]
    if stop == "cancel":
        assert response.status == "cancelled"
    else:
        assert isinstance(response.data, dict)
        assert response.data["status_error"] == {
            "status": "error",
            "error_type": "PermissionDenied",
        }
