"""发送回执的确定性、重复提交防护和部分成功提示回归。"""

import pytest
from test_napcat import Bot, registry

from qq_agent_harness.contracts import JsonObject, JsonValue, ToolContext
from qq_agent_harness.napcat.connection import NapCatError

SEND_TIMEOUT = (
    "Timeout: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg "
    "ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate EventRet:\n{}\n"
)


async def test_send_confirmation_timeout_is_unknown_and_blocks_duplicate_across_turns() -> None:
    async def remote(_action: str, _params: JsonObject) -> JsonValue:
        raise NapCatError(SEND_TIMEOUT, 1200)

    async def read(_path: str) -> bytes:
        return b"image-bytes"

    bot = Bot(remote)
    tools, context, _ = registry(bot)
    context.read_workspace = read
    payload: JsonObject = {"parts": [{"type": "image", "workspace_path": "chart.png"}]}
    result = await tools.execute(context, "qq__send_message", payload)
    assert result.status == "unknown" and result.error_type == "NapCat:1200"
    assert isinstance(result.data, dict)
    assert result.data["receipts"] == [
        {"action": "send_group_msg", "status": "unknown", "data": None}
    ]
    assert "已成功的前序" not in result.message
    assert len(context.unknown_effects) == 1
    assert (await tools.execute(context, "qq__send_message", payload)).status == "unknown"
    next_turn = ToolContext(
        context.scope,
        "next-turn",
        bot,
        read_workspace=read,
        unknown_effects=context.unknown_effects,
    )
    assert (await tools.execute(next_turn, "qq__send_message", payload)).status == "unknown"
    assert len(bot.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        "发送被拒绝：当前群禁止发送该内容",
        SEND_TIMEOUT.replace("Timeout:", "EventChecker Failed:"),
        SEND_TIMEOUT.replace("NodeIKernelMsgService/sendMsg", "NodeIKernelUserService/fetchUser"),
    ],
)
async def test_other_napcat_errors_remain_errors_and_allow_retry(error: str) -> None:
    attempts = 0

    async def remote(_action: str, _params: JsonObject) -> JsonValue:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NapCatError(error, 1200)
        return {"message_id": "retry-success"}

    bot = Bot(remote)
    tools, context, _ = registry(bot)
    payload: JsonObject = {"parts": [{"type": "text", "text": "重试同一请求"}]}
    result = await tools.execute(context, "qq__send_message", payload)
    assert result.status == "error" and result.error_type == "NapCat:1200"
    assert not context.unknown_effects
    assert "已成功的前序" not in result.message
    assert (await tools.execute(context, "qq__send_message", payload)).status == "ok"
    assert len(bot.calls) == 2


@pytest.mark.parametrize("successful_prefix", [False, True])
async def test_partial_success_warning_requires_success_in_the_current_request(
    successful_prefix: bool,
) -> None:
    async def remote(action: str, _params: JsonObject) -> JsonValue:
        if action == "send_group_msg":
            return {"message_id": "sent"}
        raise NapCatError("戳一戳被拒绝", 1200)

    bot = Bot(remote)
    tools, context, _ = registry(bot)
    # 前一次独立发送成功，不能让当前请求首段失败被误报为部分成功。
    assert (
        await tools.execute(
            context, "qq__send_message", {"parts": [{"type": "text", "text": "较早的发送"}]}
        )
    ).status == "ok"
    parts: list[JsonValue] = []
    if successful_prefix:
        parts.append({"type": "text", "text": "当前请求的前半段"})
    parts.extend(
        [
            {"type": "poke", "user_id": "20"},
            {"type": "text", "text": "失败后的后半段不应发送"},
        ]
    )
    result = await tools.execute(context, "qq__send_message", {"parts": parts})
    assert result.status == "error" and isinstance(result.data, dict)
    receipts = result.data["receipts"]
    assert isinstance(receipts, list)
    assert [item["status"] for item in receipts if isinstance(item, dict)] == (
        ["ok", "error"] if successful_prefix else ["error"]
    )
    assert ("已成功的前序" in result.message) is successful_prefix
    assert [action for action, _params in bot.calls] == (
        ["send_group_msg", "send_group_msg", "group_poke"]
        if successful_prefix
        else ["send_group_msg", "group_poke"]
    )
