"""NapCat API 载荷与 Action 回包调度测试。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from collections.abc import Sequence
from datetime import datetime
from typing import cast, override
from unittest.mock import patch

from fastapi import WebSocket

from app.api import BOTClient
from app.api.mixins.base import BaseMixin
from app.database import GroupDataScope
from app.models import (
    JsonObject,
    MessageSegment,
    NapCatId,
    Node,
    Response,
    Text,
    to_json_value,
)
from app.services.napcat import MediaStore, InlineImageArchiver


class FakeSentMessageRecorder:
    """测试用出站群消息记录器。"""

    async def record_sent(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        segments: Sequence[MessageSegment],
        occurred_at: datetime | None = None,
    ) -> None:
        """接受一次出站群消息记录调用。"""


class RecordingClient(BOTClient):
    """记录 Action 载荷的测试客户端。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self._image_temp_dir = tempfile.TemporaryDirectory()
        super().__init__(
            websocket=cast(WebSocket, FakeWebSocket()),
            sent_message_recorder=FakeSentMessageRecorder(),
            inline_image_archiver=InlineImageArchiver(
                store=MediaStore(
                    root=Path(self._image_temp_dir.name),
                    max_media_bytes=1024 * 1024,
                )
            ),
            action_timeout_seconds=120,
            send_max_attempts=5,
            send_retry_delay_seconds=0,
            send_retry_max_delay_seconds=10,
            response_summary_max_chars=500,
            persistence_retry_delays_seconds=(0.25,),
        )
        self.boot_id = "10000"
        self.action_calls: list[tuple[str, JsonObject | None]] = []

    @override
    async def _call_action(
        self, action: str, params: JsonObject | None = None
    ) -> Response:
        """记录需要回包的 Action。"""
        self.action_calls.append((action, params))
        if action == "send_group_forward_msg":
            return Response(
                status="ok",
                retcode=0,
                data={"message_id": "300", "forward_id": "forward-300"},
            )
        return Response(status="ok", retcode=0, data={"action": action})

    def last_action_params(self) -> JsonObject:
        """返回最近一次回包 Action 的参数。"""
        params = self.action_calls[-1][1]
        if params is None:
            raise AssertionError("最近一次回包 Action 没有参数")
        return params


class FakeWebSocket:
    """记录 WebSocket 发送文本的测试对象。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.sent_texts: list[str] = []

    async def send_text(self, data: str) -> None:
        """记录发送文本。"""
        self.sent_texts.append(data)


class ActionClient(BaseMixin):
    """使用真实 BaseMixin 调度逻辑的测试客户端。"""

    def __init__(self) -> None:
        """初始化 Action 回包测试状态。"""
        self.fake_websocket = FakeWebSocket()
        self.websocket = cast(WebSocket, self.fake_websocket)
        self.sent_message_recorder = FakeSentMessageRecorder()
        self.echo_dict: dict[str, asyncio.Future[Response]] = {}
        self.persistence_failed_event = asyncio.Event()
        self.boot_id: NapCatId = "10000"
        self.timeout = 1

    async def call_action_for_test(self) -> Response:
        return await self._call_action("get_stranger_info", {"user_id": "20000"})


def parse_payload(raw_text: str) -> JsonObject:
    """把 WebSocket 文本解析为 JSON 对象。"""
    raw_payload = cast(object, json.loads(raw_text))
    if not isinstance(raw_payload, dict):
        raise AssertionError("WebSocket 载荷必须是 JSON 对象")
    payload: JsonObject = {}
    raw_items = cast(dict[object, object], raw_payload)
    for key, value in raw_items.items():
        if not isinstance(key, str):
            raise AssertionError("WebSocket 载荷键必须是字符串")
        payload[key] = to_json_value(value)
    return payload


def extract_echo(payload: JsonObject) -> str:
    """从测试载荷中提取 echo。"""
    raw_echo = payload.get("echo")
    if not isinstance(raw_echo, str):
        raise AssertionError("测试载荷缺少字符串 echo")
    return raw_echo


class NapCatProtocolAlignmentTest(unittest.IsolatedAsyncioTestCase):
    """验证 4.18.2 Action 参数不会继续漂移。"""

    async def test_protocol_parameter_alignment(self) -> None:
        """保留账号查询的 no_cache 字段及撤回消息的回包语义。"""
        client = RecordingClient()

        _ = await client.get_stranger_info("200")
        params = client.last_action_params()
        self.assertIs(params["no_cache"], False)

        response = await client.delete_msg_with_response("300")
        params = client.last_action_params()
        self.assertEqual(response.status, "ok")
        self.assertEqual(params["message_id"], "300")
        self.assertEqual(client.action_calls[-1][0], "delete_msg")

    async def test_send_group_forward_msg_uses_napcat_forward_action(self) -> None:
        """发送群合并转发时使用 NapCat 的专用 action 和 messages 参数。"""
        client = RecordingClient()

        response = await client.send_group_forward_msg(
            group_id="100",
            messages=[
                Node.new(
                    user_id="10000",
                    nickname="机器人",
                    content=[Text.new("长回复正文")],
                )
            ],
        )

        action, params = client.action_calls[-1]
        self.assertEqual(response.status, "ok")
        self.assertEqual(action, "send_group_forward_msg")
        self.assertIsNotNone(params)
        if params is None:
            raise AssertionError("合并转发调用必须包含参数")
        self.assertEqual(params["group_id"], "100")
        self.assertEqual(
            params["messages"],
            [
                {
                    "type": "node",
                    "data": {
                        "user_id": "10000",
                        "nickname": "机器人",
                        "content": [
                            {"type": "text", "data": {"text": "长回复正文"}}
                        ],
                    },
                }
            ],
        )


class NapCatActionDispatchTest(unittest.IsolatedAsyncioTestCase):
    """验证 Action 提前回包和发送失败后的等待对象清理。"""

    async def test_action_accepts_response_before_send_returns(self) -> None:
        """发送期间到达的回包不会丢失，完成后释放等待对象。"""
        client = ActionClient()

        async def respond_immediately(raw: str) -> None:
            echo = extract_echo(parse_payload(raw))
            await client.receive_data(Response(status="ok", retcode=0, echo=echo))

        with patch.object(client.fake_websocket, "send_text", side_effect=respond_immediately):
            result = await client.call_action_for_test()
        self.assertEqual(result.status, "ok")
        self.assertEqual(client.echo_dict, {})

        with patch.object(client.fake_websocket, "send_text", side_effect=RuntimeError("closed")):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                await client.call_action_for_test()
        self.assertEqual(client.echo_dict, {})
