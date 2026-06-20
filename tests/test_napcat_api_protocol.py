"""NapCat API 载荷与 Stream 调度测试。"""

import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast, override

from fastapi import WebSocket

from app.api import BOTClient
from app.api.mixins.base import BaseMixin
from app.database import RedisDatabaseManager
from app.models import (
    JsonObject,
    NapCatId,
    Node,
    Response,
    StreamTransferResult,
    Text,
    to_json_value,
)


class RecordingClient(BOTClient):
    """记录 Action 载荷的测试客户端。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.action_calls: list[tuple[str, JsonObject | None]] = []
        self.send_calls: list[tuple[str, JsonObject | None]] = []

    @override
    async def _call_action(
        self, action: str, params: JsonObject | None = None
    ) -> Response:
        """记录需要回包的 Action。"""
        self.action_calls.append((action, params))
        if action == "upload_file_stream" and params is not None:
            if params.get("is_complete") is True:
                return Response(
                    status="ok",
                    retcode=0,
                    data={"file_path": "stream-cache-path"},
                )
        return Response(status="ok", retcode=0, data={"action": action})

    @override
    async def _send_action(
        self, action: str, params: JsonObject | None = None
    ) -> None:
        """记录无需回包的 Action。"""
        self.send_calls.append((action, params))

    def last_action_params(self) -> JsonObject:
        """返回最近一次回包 Action 的参数。"""
        params = self.action_calls[-1][1]
        if params is None:
            raise AssertionError("最近一次回包 Action 没有参数")
        return params

    def last_send_params(self) -> JsonObject:
        """返回最近一次发送 Action 的参数。"""
        params = self.send_calls[-1][1]
        if params is None:
            raise AssertionError("最近一次发送 Action 没有参数")
        return params


class FakeWebSocket:
    """记录 WebSocket 发送文本的测试对象。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.sent_texts: list[str] = []

    async def send_text(self, data: str) -> None:
        """记录发送文本。"""
        self.sent_texts.append(data)


class StreamClient(BaseMixin):
    """使用真实 BaseMixin 调度逻辑的测试客户端。"""

    def __init__(self) -> None:
        """初始化 Stream 测试状态。"""
        self.fake_websocket = FakeWebSocket()
        self.websocket = cast(WebSocket, self.fake_websocket)
        self.database = cast(RedisDatabaseManager, cast(object, None))
        self.echo_dict: dict[str, asyncio.Future[Response]] = {}
        self.stream_dict: dict[str, asyncio.Queue[Response]] = {}
        self.boot_id: NapCatId = "10000"
        self.timeout = 1

    async def call_stream_action_for_test(
        self, action: str, params: JsonObject
    ) -> StreamTransferResult:
        """通过公开测试入口触发 Stream Action 调度。"""
        return await self._call_stream_action(action, params)


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


def require_params(call: tuple[str, JsonObject | None]) -> JsonObject:
    """从调用记录中取出必需参数。"""
    params = call[1]
    if params is None:
        raise AssertionError("测试调用缺少参数")
    return params


class NapCatProtocolAlignmentTest(unittest.IsolatedAsyncioTestCase):
    """验证 4.18.2 Action 参数不会继续漂移。"""

    async def test_protocol_parameter_alignment(self) -> None:
        """修正字段与新版必填默认值会进入真实载荷。"""
        client = RecordingClient()

        await client.upload_group_file("100", "file.bin", "file.bin")
        params = client.last_send_params()
        self.assertIs(params["upload_file"], True)

        await client.upload_private_file("200", "file.bin", "file.bin")
        params = client.last_send_params()
        self.assertIs(params["upload_file"], True)

        _ = await client.download_file(url="https://example.com/file.bin")
        params = client.last_action_params()
        self.assertNotIn("thread_cnt", params)

        _ = await client.get_stranger_info("200")
        params = client.last_action_params()
        self.assertIs(params["no_cache"], False)

        _ = await client.ark_share_peer(user_id="200")
        params = client.last_action_params()
        self.assertEqual(params["phone_number"], "")
        self.assertNotIn("phoneNumber", params)

        await client.set_input_status(user_id="200", event_type=1)
        params = client.last_send_params()
        self.assertEqual(params["event_type"], 1)
        self.assertNotIn("eventType", params)
        self.assertNotIn("group_id", params)

        _ = await client.get_ai_record("character", "hello", "100")
        params = client.last_action_params()
        self.assertEqual(params["group_id"], "100")

        _ = await client.fetch_emoji_like("300", "66", "1")
        params = client.last_action_params()
        self.assertEqual(params["count"], 20)
        self.assertEqual(params["cookie"], "")

        _ = await client.get_group_msg_history("100")
        params = client.last_action_params()
        self.assertIs(params["reverse_order"], False)
        self.assertIs(params["reverseOrder"], False)
        self.assertIs(params["disable_get_url"], False)
        self.assertIs(params["parse_mult_msg"], True)
        self.assertIs(params["quick_reply"], False)

        _ = await client.get_friend_msg_history("200")
        params = client.last_action_params()
        self.assertIs(params["reverse_order"], False)
        self.assertIs(params["reverseOrder"], False)
        self.assertIs(params["disable_get_url"], False)
        self.assertIs(params["parse_mult_msg"], True)
        self.assertIs(params["quick_reply"], False)

        await client.set_group_special_title("100", "200", "头衔")
        params = client.last_send_params()
        self.assertNotIn("duration", params)

        _ = await client.get_group_system_msg()
        params = client.last_action_params()
        self.assertEqual(params["count"], 50)

        _ = await client.get_group_ignored_notifies()
        action, params_or_none = client.action_calls[-1]
        self.assertEqual(action, "get_group_ignored_notifies")
        self.assertIsNone(params_or_none)

        await client.set_group_kick_members("100", ["200", "300"])
        params = client.last_send_params()
        self.assertEqual(params["user_id"], ["200", "300"])
        self.assertNotIn("user_ids", params)

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


class NapCatStreamDispatchTest(unittest.IsolatedAsyncioTestCase):
    """验证 Stream Action 的 echo 分流与终止条件。"""

    async def test_stream_action_collects_packets_until_response(self) -> None:
        """多包 stream-action 会按顺序收集并在完成包后清理队列。"""
        client = StreamClient()
        task = asyncio.create_task(
            client.call_stream_action_for_test(
                "download_file_stream", {"file": "a.bin"}
            )
        )
        await asyncio.sleep(0)
        payload = parse_payload(client.fake_websocket.sent_texts[-1])
        echo = extract_echo(payload)

        first_packet = Response(
            status="ok",
            retcode=0,
            data={"type": "stream", "data": "part-1"},
            echo=echo,
            stream="stream-action",
        )
        final_packet = Response(
            status="ok",
            retcode=0,
            data={"type": "response", "data": "done"},
            echo=echo,
            stream="stream-action",
        )
        await client.receive_data(first_packet)
        await client.receive_data(final_packet)

        result = await task

        self.assertEqual(result.packets, [first_packet, final_packet])
        self.assertEqual(result.final_response, final_packet)
        self.assertNotIn(echo, client.stream_dict)

    async def test_stream_action_error_packet_finishes_transfer(self) -> None:
        """失败包会终止收集并作为最终响应返回。"""
        client = StreamClient()
        task = asyncio.create_task(
            client.call_stream_action_for_test("test_download_stream", {"error": True})
        )
        await asyncio.sleep(0)
        payload = parse_payload(client.fake_websocket.sent_texts[-1])
        echo = extract_echo(payload)

        error_packet = Response(
            status="failed",
            retcode=200,
            data={"type": "error", "data_type": "error"},
            message="测试失败",
            echo=echo,
            stream="stream-action",
        )
        await client.receive_data(error_packet)

        result = await task

        self.assertEqual(result.packets, [error_packet])
        self.assertEqual(result.final_response, error_packet)
        self.assertNotIn(echo, client.stream_dict)

    async def test_stream_call_path_accepts_normal_single_response(self) -> None:
        """普通单包响应也能通过 Stream 调用路径结束。"""
        client = StreamClient()
        task = asyncio.create_task(
            client.call_stream_action_for_test(
                "download_file_stream", {"file": "a.bin"}
            )
        )
        await asyncio.sleep(0)
        payload = parse_payload(client.fake_websocket.sent_texts[-1])
        echo = extract_echo(payload)

        packet = Response(
            status="ok",
            retcode=0,
            data={"file": "a.bin"},
            echo=echo,
            stream="normal-action",
        )
        await client.receive_data(packet)

        result = await task

        self.assertEqual(result.packets, [packet])
        self.assertEqual(result.final_response, packet)
        self.assertNotIn(echo, client.stream_dict)


class NapCatStreamUploadHelperTest(unittest.IsolatedAsyncioTestCase):
    """验证本地 Stream 上传 helper 的协议载荷。"""

    async def test_upload_local_file_stream_chunks_file_and_finishes(self) -> None:
        """本地文件会异步分片、计算 SHA256，并发送完成包。"""
        content = b"abcdefg"
        expected_sha256 = hashlib.sha256(content).hexdigest()
        client = RecordingClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "sample.bin"
            _ = file_path.write_bytes(content)

            response = await client.upload_local_file_stream(
                path=file_path, chunk_size=3, file_retention=12345
            )

        upload_calls = [
            call for call in client.action_calls if call[0] == "upload_file_stream"
        ]
        self.assertEqual(len(upload_calls), 4)
        chunk_params = [require_params(call) for call in upload_calls[:3]]
        complete_params = require_params(upload_calls[-1])
        decoded_chunks = [
            base64.b64decode(self._require_string(params["chunk_data"]))
            for params in chunk_params
        ]

        self.assertEqual(decoded_chunks, [b"abc", b"def", b"g"])
        for index, params in enumerate(chunk_params):
            self.assertEqual(params["chunk_index"], index)
            self.assertEqual(params["total_chunks"], 3)
            self.assertEqual(params["file_size"], len(content))
            self.assertEqual(params["expected_sha256"], expected_sha256)
            self.assertEqual(params["filename"], "sample.bin")
            self.assertEqual(params["file_retention"], 12345)
        self.assertIs(complete_params["is_complete"], True)
        self.assertEqual(complete_params["file_retention"], 12345)
        self.assertEqual(response.data, {"file_path": "stream-cache-path"})

    def _require_string(self, value: object) -> str:
        """把测试 JSON 字段收窄为字符串。"""
        if not isinstance(value, str):
            raise AssertionError("测试字段必须是字符串")
        return value
