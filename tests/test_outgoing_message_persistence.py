"""机器人出站消息持久化测试。"""

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
from redis.asyncio import Redis

from app.api.mixins.message import MessageMixin
from app.database import RedisDatabaseManager
from app.models import GroupMessage, Image, MessageSegment, NapCatId, Response
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

    def __init__(self, database: RecordingDatabase) -> None:
        """初始化假客户端。"""
        self.database = cast(RedisDatabaseManager, database)
        self.boot_id = "10000"
        self.sent_actions: list[tuple[str, JsonObject | None]] = []

    async def _call_action(
        self, action: str, params: JsonObject | None = None
    ) -> Response:
        """模拟 NapCat 返回发送成功的消息 ID。"""
        self.sent_actions.append((action, params))
        return Response(status="ok", retcode=0, data={"message_id": 90000})


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
