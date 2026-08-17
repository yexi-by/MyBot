"""机器人出站群消息持久化测试。"""

import asyncio
import base64
import tempfile
import unittest
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast, override
from unittest.mock import AsyncMock, patch

from fastapi import WebSocket

from app.api.mixins.message import MessageMixin, NapCatSendMessageError
from app.database import GroupDataScope
from app.models import Forward, Image, JsonObject, MessageSegment, Node, Response, Text
from app.services.napcat import ImageStore, InlineImageArchiver


PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+"
    "/p9sAAAAASUVORK5CYII="
)


@dataclass(frozen=True, slots=True)
class OutgoingRecord:
    """一次出站群消息记录调用。"""

    scope: GroupDataScope
    message_id: str
    segments: tuple[MessageSegment, ...]
    occurred_at: datetime | None


class FakeSentMessageRecorder:
    """可注入失败并记录调用的出站消息仓库。"""

    def __init__(self, failures: Sequence[Exception] = ()) -> None:
        """初始化预置失败和调用记录。"""
        self.failures: list[Exception] = list(failures)
        self.calls: list[OutgoingRecord] = []

    async def record_sent(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        segments: Sequence[MessageSegment],
        occurred_at: datetime | None = None,
    ) -> None:
        """记录调用，并按顺序抛出预置异常。"""
        record = OutgoingRecord(
            scope=scope,
            message_id=message_id,
            segments=tuple(segments),
            occurred_at=occurred_at,
        )
        self.calls.append(record)
        if self.failures:
            raise self.failures.pop(0)


class FakeWebSocket:
    """记录发送和关闭操作的 WebSocket。"""

    def __init__(self) -> None:
        """初始化记录。"""
        self.sent_texts: list[str] = []
        self.close_calls: list[tuple[int, str | None]] = []

    async def send_text(self, data: str) -> None:
        """记录 Action 载荷。"""
        self.sent_texts.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        """记录会话关闭原因。"""
        self.close_calls.append((code, reason))


class FakeMessageClient(MessageMixin):
    """测试用消息客户端。"""

    def __init__(
        self,
        *,
        recorder: FakeSentMessageRecorder,
        image_root: Path,
        responses: Sequence[Response | Exception] = (),
    ) -> None:
        """初始化录制器、WebSocket 和 NapCat 响应。"""
        self.sent_message_recorder = recorder
        self.inline_image_archiver = InlineImageArchiver(
            store=ImageStore(root=image_root, max_image_bytes=1024 * 1024)
        )
        self.boot_id = "10000"
        self.fake_websocket = FakeWebSocket()
        self.websocket = cast(WebSocket, self.fake_websocket)
        self.persistence_failed_event = asyncio.Event()
        self.echo_dict: dict[str, asyncio.Future[Response]] = {}
        self.stream_dict: dict[str, asyncio.Queue[Response]] = {}
        self.sent_actions: list[tuple[str, JsonObject | None]] = []
        self.responses: list[Response | Exception] = list(responses) or [
            Response(status="ok", retcode=0, data={"message_id": 90000})
        ]
        self.send_max_attempts = 3
        self.send_retry_delay_seconds = 0
        self.timeout = 1

    @override
    async def _call_action(
        self, action: str, params: JsonObject | None = None
    ) -> Response:
        """在会话健康时按预置顺序返回 NapCat 响应。"""
        self._ensure_persistence_healthy()
        self.sent_actions.append((action, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OutgoingMessagePersistenceTest(unittest.IsolatedAsyncioTestCase):
    """验证只有 NapCat 确认发送的群消息会写入仓库。"""

    def setUp(self) -> None:
        """为每个用例创建独立的图片归档目录。"""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.image_root = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        """删除测试图片目录。"""
        self._temp_dir.cleanup()

    async def test_successful_group_message_is_recorded(self) -> None:
        """群消息发送成功后写入正确的 bot 和群范围。"""
        recorder = FakeSentMessageRecorder()
        client = FakeMessageClient(recorder=recorder, image_root=self.image_root)

        response = await client.send_msg(group_id="40000", text="你好")

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(recorder.calls), 1)
        record = recorder.calls[0]
        self.assertEqual(record.scope, GroupDataScope(bot_id="10000", group_id="40000"))
        self.assertEqual(record.message_id, "90000")
        self.assertEqual([segment.type for segment in record.segments], ["text"])

    async def test_private_message_is_not_recorded(self) -> None:
        """私聊发送成功也不写入群消息仓库。"""
        recorder = FakeSentMessageRecorder()
        client = FakeMessageClient(recorder=recorder, image_root=self.image_root)

        response = await client.send_msg(user_id="50000", text="你好")

        self.assertEqual(response.status, "ok")
        self.assertEqual(recorder.calls, [])

    async def test_persistence_retries_once_after_250ms(self) -> None:
        """首次 PostgreSQL 写入失败时等待 250ms 后只重试一次。"""
        recorder = FakeSentMessageRecorder(failures=[RuntimeError("第一次失败")])
        client = FakeMessageClient(recorder=recorder, image_root=self.image_root)
        sleep_mock = AsyncMock()

        with patch("app.api.mixins.message.asyncio.sleep", sleep_mock):
            response = await client.send_msg(group_id="40000", text="你好")

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(recorder.calls), 2)
        sleep_mock.assert_awaited_once_with(0.25)
        self.assertEqual(client.fake_websocket.close_calls, [])

    async def test_repeated_persistence_failure_closes_session_but_keeps_send_success(
        self,
    ) -> None:
        """连续写入失败不伪装 QQ 发送失败，但终止当前会话。"""
        recorder = FakeSentMessageRecorder(
            failures=[RuntimeError("第一次失败"), RuntimeError("第二次失败")]
        )
        client = FakeMessageClient(recorder=recorder, image_root=self.image_root)

        with patch("app.api.mixins.message.asyncio.sleep", AsyncMock()):
            response = await client.send_msg(group_id="40000", text="已发出")

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(recorder.calls), 2)
        self.assertEqual(
            client.fake_websocket.close_calls,
            [(1011, "PostgreSQL 持久化失败")],
        )
        self.assertTrue(client.persistence_failed_event.is_set())
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL 持久化失败"):
            await client.delete_msg("90000")

    async def test_missing_message_id_closes_session_but_keeps_send_success(
        self,
    ) -> None:
        """NapCat 成功响应无 message_id 时无法记录，终止当前会话。"""
        recorder = FakeSentMessageRecorder()
        client = FakeMessageClient(
            recorder=recorder,
            image_root=self.image_root,
            responses=[Response(status="ok", retcode=0, data={})],
        )

        response = await client.send_msg(group_id="40000", text="已发出")

        self.assertEqual(response.status, "ok")
        self.assertEqual(recorder.calls, [])
        self.assertEqual(
            client.fake_websocket.close_calls,
            [(1011, "PostgreSQL 持久化失败")],
        )
        self.assertTrue(client.persistence_failed_event.is_set())

    async def test_group_forward_message_is_recorded(self) -> None:
        """群合并转发成功后保存带节点内容的 Forward 段。"""
        recorder = FakeSentMessageRecorder()
        client = FakeMessageClient(
            recorder=recorder,
            image_root=self.image_root,
            responses=[
                Response(
                    status="ok",
                    retcode=0,
                    data={"message_id": 90004, "forward_id": "forward-90004"},
                )
            ],
        )
        nodes = [
            Node.new(
                user_id="10000",
                nickname="机器人",
                content=[Text.new("长回复正文")],
            )
        ]

        response = await client.send_group_forward_msg(
            group_id="40000",
            messages=nodes,
        )

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(recorder.calls), 1)
        record = recorder.calls[0]
        self.assertEqual(record.message_id, "90004")
        self.assertEqual(record.scope.group_id, "40000")
        self.assertEqual(len(record.segments), 1)
        segment = record.segments[0]
        self.assertIsInstance(segment, Forward)
        forward = cast(Forward, segment)
        self.assertEqual(forward.data.id, "forward-90004")
        self.assertIsNotNone(forward.data.content)

    async def test_missing_forward_id_closes_session_but_keeps_send_success(
        self,
    ) -> None:
        """合并转发成功但无 forward_id 时无法保存节点语义，终止会话。"""
        recorder = FakeSentMessageRecorder()
        client = FakeMessageClient(
            recorder=recorder,
            image_root=self.image_root,
            responses=[
                Response(status="ok", retcode=0, data={"message_id": 90005})
            ],
        )

        response = await client.send_group_forward_msg(
            group_id="40000",
            messages=[
                Node.new(
                    user_id="10000",
                    nickname="机器人",
                    content=[Text.new("长回复正文")],
                )
            ],
        )

        self.assertEqual(response.status, "ok")
        self.assertEqual(recorder.calls, [])
        self.assertEqual(
            client.fake_websocket.close_calls,
            [(1011, "PostgreSQL 持久化失败")],
        )

    async def test_send_failure_does_not_write_database(self) -> None:
        """NapCat 发送连续失败时不写入数据库。"""
        recorder = FakeSentMessageRecorder()
        client = FakeMessageClient(
            recorder=recorder,
            image_root=self.image_root,
            responses=[
                Response(status="failed", retcode=500, message="temporary failure"),
                Response(status="failed", retcode=500, message="temporary failure"),
            ],
        )
        client.send_max_attempts = 2

        with self.assertRaises(NapCatSendMessageError):
            _ = await client.send_msg(group_id="40000", text="你好")

        self.assertEqual(recorder.calls, [])

    async def test_base64_group_image_is_archived_before_recording(self) -> None:
        """出站 base64 图片先写入内容寻址文件，持久化副本补入路径。"""
        recorder = FakeSentMessageRecorder()
        client = FakeMessageClient(recorder=recorder, image_root=self.image_root)
        source = f"base64://{PNG_BASE64}"

        response = await client.send_msg(group_id="40000", image=source)

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(recorder.calls), 1)
        segment = recorder.calls[0].segments[0]
        self.assertIsInstance(segment, Image)
        image = cast(Image, segment)
        self.assertEqual(image.data.file, source)
        self.assertIsNotNone(image.data.path)
        archived_path = Path(image.data.path or "")
        self.assertTrue(archived_path.is_file())
        self.assertEqual(archived_path.read_bytes(), base64.b64decode(PNG_BASE64))
        self.assertTrue(archived_path.is_relative_to(self.image_root.resolve()))
