"""机器人出站消息持久化测试。"""

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
from redis.asyncio import Redis

from app.api.mixins.message import MessageMixin, NapCatSendMessageError
from app.database import RedisDatabaseManager
from app.models import (
    Forward,
    GroupMessage,
    Image,
    MessageSegment,
    NapCatId,
    Node,
    Response,
    Text,
)
from app.models.common import JsonObject


PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+"
    "/p9sAAAAASUVORK5CYII="
)


class FakePipeline:
    """测试用 Redis pipeline。"""

    def __init__(self, redis: "FakeRedis") -> None:
        """初始化 pipeline 暂存区。"""
        self.redis = redis
        self.hash_items: list[tuple[str, str, str]] = []
        self.zset_items: list[tuple[str, dict[str, int | float]]] = []

    def hset(self, key: str, field: str, value: str) -> None:
        """暂存 Hash 写入。"""
        self.hash_items.append((key, field, value))

    def zadd(self, key: str, mapping: dict[str, int | float]) -> None:
        """暂存 ZSet 写入。"""
        self.zset_items.append((key, mapping))

    async def execute(self) -> list[object]:
        """提交暂存写入。"""
        for key, field, value in self.hash_items:
            self.redis.hashes.setdefault(key, {})[field] = value
        for key, mapping in self.zset_items:
            self.redis.zsets.setdefault(key, {}).update(mapping)
        return []


class FakeRedis:
    """测试用 Redis 客户端。"""

    def __init__(self) -> None:
        """初始化内存存储。"""
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, int | float]] = {}

    def pipeline(self) -> FakePipeline:
        """创建测试 pipeline。"""
        return FakePipeline(redis=self)


@dataclass
class OutgoingRecord:
    """记录一次出站消息保存调用。"""

    self_id: NapCatId
    message_id: NapCatId
    group_id: NapCatId | None
    user_id: NapCatId | None
    message_segments: list[MessageSegment]


class RecordingDatabase:
    """测试用出站消息数据库。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.records: list[OutgoingRecord] = []

    async def store_outgoing_message(
        self,
        *,
        self_id: NapCatId,
        message_id: NapCatId,
        message_segments: list[MessageSegment],
        group_id: NapCatId | None = None,
        user_id: NapCatId | None = None,
    ) -> None:
        """记录出站消息保存调用。"""
        self.records.append(
            OutgoingRecord(
                self_id=self_id,
                message_id=message_id,
                group_id=group_id,
                user_id=user_id,
                message_segments=message_segments,
            )
        )


class FakeMessageClient(MessageMixin):
    """测试用消息客户端。"""

    def __init__(
        self,
        database: RecordingDatabase,
        responses: list[Response | Exception] | None = None,
    ) -> None:
        """初始化假客户端。"""
        self.database = cast(RedisDatabaseManager, database)
        self.boot_id = "10000"
        self.sent_actions: list[tuple[str, JsonObject | None]] = []
        self.responses = responses or [
            Response(status="ok", retcode=0, data={"message_id": 90000})
        ]
        self.send_retry_count = 3
        self.send_retry_delay = 0

    async def _call_action(
        self, action: str, params: JsonObject | None = None
    ) -> Response:
        """按预置序列模拟 NapCat 响应。"""
        self.sent_actions.append((action, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OutgoingMessagePersistenceTest(unittest.IsolatedAsyncioTestCase):
    """验证机器人自己发送的消息会进入后续引用检索链路。"""

    async def test_message_mixin_records_successful_sent_group_message(self) -> None:
        """send_msg 成功后会把群消息交给数据库保存。"""
        database = RecordingDatabase()
        client = FakeMessageClient(database=database)

        response = await client.send_msg(group_id="40000", text="你好")

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(database.records), 1)
        record = database.records[0]
        self.assertEqual(record.self_id, "10000")
        self.assertEqual(record.message_id, "90000")
        self.assertEqual(record.group_id, "40000")
        self.assertIsNone(record.user_id)
        self.assertEqual([segment.type for segment in record.message_segments], ["text"])

    async def test_message_mixin_retries_failed_send_then_records_success(
        self,
    ) -> None:
        """send_msg 首次失败后重试，成功时只保存一次出站消息。"""
        database = RecordingDatabase()
        client = FakeMessageClient(
            database=database,
            responses=[
                Response(status="failed", retcode=500, message="temporary failure"),
                Response(status="ok", retcode=0, data={"message_id": 90001}),
            ],
        )

        response = await client.send_msg(group_id="40000", text="你好")

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(client.sent_actions), 2)
        self.assertEqual(len(database.records), 1)
        self.assertEqual(database.records[0].message_id, "90001")

    async def test_message_mixin_does_not_retry_action_timeout(self) -> None:
        """等待 send_msg 回包超时时不重试，避免消息实际已发出后重复发送。"""
        database = RecordingDatabase()
        client = FakeMessageClient(
            database=database,
            responses=[
                TimeoutError("等待 NapCat 响应超时"),
                Response(status="ok", retcode=0, data={"message_id": 90002}),
            ],
        )

        with self.assertRaises(NapCatSendMessageError) as raised:
            _ = await client.send_msg(group_id="40000", text="你好")

        self.assertIn("发送状态不确定", str(raised.exception))
        self.assertEqual(len(client.sent_actions), 1)
        self.assertEqual(database.records, [])

    async def test_message_mixin_does_not_retry_napcat_send_msg_timeout(
        self,
    ) -> None:
        """NapCat sendMsg 内部超时时不重试，避免同一正文重复出现在群里。"""
        database = RecordingDatabase()
        client = FakeMessageClient(
            database=database,
            responses=[
                Response(
                    status="failed",
                    retcode=1200,
                    message=(
                        "Timeout: NTEvent serviceAndMethod:"
                        "NodeIKernelMsgService/sendMsg "
                        "ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate"
                    ),
                    wording=(
                        "Timeout: NTEvent serviceAndMethod:"
                        "NodeIKernelMsgService/sendMsg "
                        "ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate"
                    ),
                ),
                Response(status="ok", retcode=0, data={"message_id": 90003}),
            ],
        )

        with self.assertRaises(NapCatSendMessageError) as raised:
            _ = await client.send_msg(group_id="40000", text="你好")

        self.assertIn("发送状态不确定", str(raised.exception))
        self.assertEqual(len(client.sent_actions), 1)
        self.assertEqual(database.records, [])

    async def test_message_mixin_raises_after_send_retries_exhausted(self) -> None:
        """send_msg 连续失败时抛出显式发送异常，不保存出站消息。"""
        database = RecordingDatabase()
        client = FakeMessageClient(
            database=database,
            responses=[
                Response(status="failed", retcode=500, message="temporary failure"),
                Response(status="failed", retcode=500, message="temporary failure"),
            ],
        )
        client.send_retry_count = 2

        with self.assertRaises(NapCatSendMessageError) as raised:
            _ = await client.send_msg(group_id="40000", text="你好")

        self.assertIn("NapCat 发送消息失败", str(raised.exception))
        self.assertEqual(len(client.sent_actions), 2)
        self.assertEqual(database.records, [])

    async def test_message_mixin_does_not_retry_missing_message_id(self) -> None:
        """发送成功但缺少 message_id 时不重试，避免实际已发送消息重复出现。"""
        database = RecordingDatabase()
        client = FakeMessageClient(
            database=database,
            responses=[Response(status="ok", retcode=0, data={})],
        )

        response = await client.send_msg(group_id="40000", text="你好")

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(client.sent_actions), 1)
        self.assertEqual(database.records, [])

    async def test_group_forward_send_records_forward_segment(self) -> None:
        """send_group_forward_msg 成功后保存合并转发段，支持后续引用。"""
        database = RecordingDatabase()
        client = FakeMessageClient(
            database=database,
            responses=[
                Response(
                    status="ok",
                    retcode=0,
                    data={"message_id": 90004, "forward_id": "forward-90004"},
                )
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
        self.assertEqual(len(database.records), 1)
        record = database.records[0]
        self.assertEqual(record.message_id, "90004")
        self.assertEqual(record.group_id, "40000")
        self.assertEqual([segment.type for segment in record.message_segments], ["forward"])
        forward_segment = record.message_segments[0]
        self.assertIsInstance(forward_segment, Forward)
        forward_segment = cast(Forward, forward_segment)
        self.assertEqual(forward_segment.data.id, "forward-90004")

    async def test_database_stores_outgoing_base64_image_as_cached_group_message(
        self,
    ) -> None:
        """Base64 出站图片会落成本地文件，并以 message_sent 写入 Redis。"""
        fake_redis = FakeRedis()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = RedisDatabaseManager(
                client=cast(httpx.AsyncClient, object()),
                path=temp_dir,
                redis_client=cast(Redis, fake_redis),
                consumers_count=0,
            )
            await manager.store_outgoing_message(
                self_id="10000",
                group_id="40000",
                message_id="90000",
                message_segments=[Image.new(f"base64://{PNG_BASE64}")],
            )

            key = "bot:10000:group:40000:msg_data"
            raw_message = fake_redis.hashes[key]["90000"]
            stored_message = GroupMessage.model_validate_json(raw_message)
            stored_segment = stored_message.message[0]

            self.assertEqual(stored_message.post_type, "message_sent")
            self.assertEqual(stored_message.user_id, "10000")
            self.assertIsInstance(stored_segment, Image)
            stored_image = cast(Image, stored_segment)
            self.assertFalse(stored_image.data.file.startswith("base64://"))
            self.assertIsNotNone(stored_image.data.path)
            self.assertTrue(Path(stored_image.data.path or "").is_file())
