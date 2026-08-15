"""PostgreSQL 群消息仓库集成测试。"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select, update

from app.database import (
    DatabaseMigrator,
    GroupDataScope,
    PostgreSQLMessageRepository,
    PostgreSQLRuntime,
)
from app.database.models import GroupMessageImageRow, GroupMessageRow
from app.models import (
    Forward,
    GroupMessage,
    Image,
    MessageSegment,
    Sender,
    StoredImage,
    Text,
    Video,
)
from app.services.napcat.image_archive import ImageArchiveTaskRepository

TEST_DATABASE_ENV = "MYBOT_TEST_DATABASE_URL"


class PostgreSQLRepositoryTest(unittest.IsolatedAsyncioTestCase):
    """在真实 PostgreSQL 上验证查询、归档和图片租约。"""

    async def asyncSetUp(self) -> None:
        """为每个用例创建独立 bot 数据范围。"""
        database_url = os.environ.get(TEST_DATABASE_ENV)
        if database_url is None:
            self.skipTest(f"未配置 {TEST_DATABASE_ENV}，跳过 PostgreSQL 集成测试")
        await DatabaseMigrator(database_url=database_url).upgrade_all()
        self.runtime = PostgreSQLRuntime.create(database_url=database_url)
        self.bot_id = f"testbot-{uuid4().hex}"
        self.scope = GroupDataScope(bot_id=self.bot_id, group_id="group-1")
        self.image_root = Path("test-images")
        self.repository = PostgreSQLMessageRepository(
            session_factory=self.runtime.session_factory,
            image_root=self.image_root,
        )
        archive_repository: ImageArchiveTaskRepository = self.repository
        self.assertIs(archive_repository, self.repository)

    async def asyncTearDown(self) -> None:
        """只清理本用例的随机 bot 记录。"""
        runtime = getattr(self, "runtime", None)
        bot_id = getattr(self, "bot_id", None)
        if isinstance(runtime, PostgreSQLRuntime) and isinstance(bot_id, str):
            async with runtime.session_factory() as session, session.begin():
                _ = await session.execute(
                    delete(GroupMessageRow).where(GroupMessageRow.bot_id == bot_id)
                )
            await runtime.dispose()

    async def test_duplicate_message_keeps_first_evidence_and_scopes_are_isolated(
        self,
    ) -> None:
        """重复事件不产生重复行或改写首份证据，且其他 bot 无法读取。"""
        first = self._message(message_id="same", text="旧内容")
        second = self._message(message_id="same", text="新内容")

        first_write_result = await self.repository.save_incoming(first)
        self.assertIsNone(first_write_result)
        first_stored = await self.repository.get_active(
            scope=self.scope,
            message_id="same",
        )
        await self.repository.save_incoming(second)
        second_stored = await self.repository.get_active(
            scope=self.scope,
            message_id="same",
        )

        self.assertIsNotNone(first_stored)
        self.assertIsNotNone(second_stored)
        if first_stored is None or second_stored is None:
            self.fail("重复写入后消息应该可读")
        self.assertEqual(first_stored.row_id, second_stored.row_id)
        self.assertEqual(second_stored.segments[0], Text.new("旧内容"))
        hidden = await self.repository.get_active(
            scope=GroupDataScope(bot_id="another-bot", group_id=self.scope.group_id),
            message_id="same",
        )
        self.assertIsNone(hidden)

    async def test_queries_use_stable_order_cursor_sender_and_time_range(self) -> None:
        """同秒消息按行 ID 稳定排序，筛选由 SQL 完成。"""
        base_time = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
        for index in range(5):
            await self.repository.save_incoming(
                self._message(
                    message_id=f"m{index}",
                    text=str(index),
                    occurred_at=base_time,
                    sender_id="alice" if index % 2 == 0 else "bob",
                )
            )

        newest = await self.repository.list_recent(scope=self.scope, limit=2)
        self.assertEqual([item.message_id for item in newest], ["m4", "m3"])
        older = await self.repository.list_recent(
            scope=self.scope,
            limit=2,
            before=newest[-1].cursor,
        )
        self.assertEqual([item.message_id for item in older], ["m2", "m1"])
        alice = await self.repository.list_between(
            scope=self.scope,
            start=base_time,
            end=base_time + timedelta(seconds=1),
            limit=10,
            sender_id="alice",
        )
        self.assertEqual([item.message_id for item in alice], ["m4", "m2", "m0"])
        around = await self.repository.list_around(
            scope=self.scope,
            message_id="m2",
            before_count=1,
            after_count=1,
        )
        self.assertEqual([item.message_id for item in around], ["m1", "m2", "m3"])

    async def test_list_around_filters_sender_before_limiting_same_second_rows(
        self,
    ) -> None:
        """成员筛选先于两侧限量，同秒消息仍按行 ID 稳定排序。"""
        occurred_at = datetime(2026, 8, 16, 10, 3, tzinfo=UTC)
        senders = (
            "alice",
            "bob",
            "alice",
            "bob",
            "bob",
            "bob",
            "alice",
            "bob",
            "alice",
        )
        for index, sender_id in enumerate(senders):
            await self.repository.save_incoming(
                self._message(
                    message_id=f"same-second-{index}",
                    occurred_at=occurred_at,
                    sender_id=sender_id,
                )
            )

        alice_messages = await self.repository.list_around(
            scope=self.scope,
            message_id="same-second-4",
            before_count=2,
            after_count=2,
            sender_id="alice",
        )

        self.assertEqual(
            [item.message_id for item in alice_messages],
            ["same-second-0", "same-second-2", "same-second-6", "same-second-8"],
        )
        bob_messages = await self.repository.list_around(
            scope=self.scope,
            message_id="same-second-4",
            before_count=1,
            after_count=1,
            sender_id="bob",
        )
        self.assertEqual(
            [item.message_id for item in bob_messages],
            ["same-second-3", "same-second-4", "same-second-5"],
        )

    async def test_list_around_locates_anchor_outside_recent_messages(self) -> None:
        """任意旧锚点不依赖最近消息窗口也能返回紧邻前后文。"""
        anchor_time = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
        await self.repository.save_incoming(
            self._message(
                message_id="before-old-anchor",
                occurred_at=anchor_time - timedelta(seconds=1),
            )
        )
        await self.repository.save_incoming(
            self._message(message_id="old-anchor", occurred_at=anchor_time)
        )
        for index in range(32):
            await self.repository.save_incoming(
                self._message(
                    message_id=f"newer-{index}",
                    occurred_at=anchor_time + timedelta(seconds=index + 1),
                )
            )

        messages = await self.repository.list_around(
            scope=self.scope,
            message_id="old-anchor",
            before_count=1,
            after_count=2,
        )

        self.assertEqual(
            [item.message_id for item in messages],
            ["before-old-anchor", "old-anchor", "newer-0", "newer-1"],
        )

    async def test_incoming_replay_preserves_original_body_and_image_evidence(
        self,
    ) -> None:
        """删图、同序号换图和撤回后重放都不能改写首次入站证据。"""
        message_id = "incoming-evidence"
        original = self._message(
            message_id=message_id,
            segments=[
                Text.new("首次正文"),
                Image.new(
                    "original.png",
                    file_id="original-file-id",
                    path="C:/temporary/original.png",
                    url="https://example.invalid/original.png",
                ),
            ],
        )
        await self.repository.save_incoming(original)
        first = await self.repository.get_active(
            scope=self.scope,
            message_id=message_id,
        )
        self.assertIsNotNone(first)
        if first is None:
            self.fail("首次入站消息应该可读")
        original_row_id = first.row_id
        original_image_row_id = first.images[0].row_id
        task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        self.assertEqual(task.task_id, original_image_row_id)
        self.assertTrue(
            await self.repository.complete(
                task_id=task.task_id,
                lease_token=task.lease_token,
                image=StoredImage(
                    storage_key="ef/original.png",
                    mime_type="image/png",
                    size_bytes=24,
                ),
            )
        )

        await self.repository.save_incoming(
            self._message(
                message_id=message_id,
                segments=[Text.new("重放时删除图片")],
            )
        )
        after_delete = await self.repository.get_active(
            scope=self.scope,
            message_id=message_id,
        )
        self.assertIsNotNone(after_delete)
        if after_delete is None:
            self.fail("删图重放后首次消息应该仍可读")
        self.assertEqual(after_delete.row_id, original_row_id)
        self.assertEqual(after_delete.segments[0], Text.new("首次正文"))
        self.assertEqual(len(after_delete.segments), 2)
        self.assertEqual(after_delete.images[0].row_id, original_image_row_id)
        self.assertEqual(after_delete.images[0].status, "stored")

        replacement = self._message(
            message_id=message_id,
            segments=[
                Text.new("重放时替换正文"),
                Image.new(
                    "replacement.png",
                    file_id="replacement-file-id",
                    path="C:/temporary/replacement.png",
                    url="https://example.invalid/replacement.png",
                ),
            ],
        )
        await self.repository.save_incoming(replacement)
        after_replacement = await self.repository.get_active(
            scope=self.scope,
            message_id=message_id,
        )
        self.assertIsNotNone(after_replacement)
        if after_replacement is None:
            self.fail("换图重放后首次消息应该仍可读")
        self.assertEqual(after_replacement.row_id, original_row_id)
        self.assertEqual(after_replacement.segments[0], Text.new("首次正文"))
        image_segment = after_replacement.segments[1]
        self.assertIsInstance(image_segment, Image)
        if isinstance(image_segment, Image):
            self.assertEqual(image_segment.data.file, "original.png")
            self.assertEqual(
                image_segment.data.url,
                "https://example.invalid/original.png",
            )
        self.assertEqual(
            after_replacement.images[0].file_id,
            "original-file-id",
        )

        recalled_at = datetime(2026, 8, 16, 10, 2, tzinfo=UTC)
        self.assertTrue(
            await self.repository.archive(
                scope=self.scope,
                message_id=message_id,
                recalled_at=recalled_at,
                recalled_by_id="operator",
            )
        )
        await self.repository.save_incoming(replacement)
        self.assertIsNone(
            await self.repository.get_active(
                scope=self.scope,
                message_id=message_id,
            )
        )

        async with self.runtime.session_factory() as session:
            archived_row = await session.scalar(
                select(GroupMessageRow).where(
                    GroupMessageRow.id == original_row_id,
                )
            )
            image_rows = list(
                await session.scalars(
                    select(GroupMessageImageRow)
                    .where(GroupMessageImageRow.message_row_id == original_row_id)
                    .order_by(GroupMessageImageRow.segment_index)
                )
            )
        self.assertIsNotNone(archived_row)
        if archived_row is None:
            self.fail("撤回归档行应该保留")
        self.assertEqual(archived_row.recalled_at, recalled_at)
        self.assertEqual(archived_row.recalled_by_id, "operator")
        self.assertEqual(
            archived_row.segments,
            [
                {"type": "text", "data": {"text": "首次正文"}},
                {
                    "type": "image",
                    "data": {
                        "file": "original.png",
                        "url": "https://example.invalid/original.png",
                        "file_id": "original-file-id",
                    },
                },
            ],
        )
        self.assertEqual(len(image_rows), 1)
        image_row = image_rows[0]
        self.assertEqual(image_row.id, original_image_row_id)
        self.assertEqual(image_row.segment_index, 1)
        self.assertEqual(image_row.source_file, "original.png")
        self.assertIsNone(image_row.source_path)
        self.assertEqual(
            image_row.source_url,
            "https://example.invalid/original.png",
        )
        self.assertEqual(image_row.file_id, "original-file-id")
        self.assertEqual(image_row.status, "stored")
        self.assertEqual(image_row.storage_key, "ef/original.png")

    async def test_recall_is_idempotent_hidden_and_survives_late_echo(self) -> None:
        """撤回后普通读取不可见，迟到 echo 不清除首次证据。"""
        message = self._message(message_id="recalled", text="需要保留的原文")
        await self.repository.save_incoming(message)
        stored = await self.repository.get_active(
            scope=self.scope,
            message_id="recalled",
        )
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("撤回前消息应该可读")
        first_time = datetime(2026, 8, 16, 10, 1, tzinfo=UTC)

        self.assertTrue(
            await self.repository.archive(
                scope=self.scope,
                message_id=stored.message_id,
                recalled_at=first_time,
                recalled_by_id="operator-1",
            )
        )
        self.assertTrue(
            await self.repository.archive(
                scope=self.scope,
                message_id=stored.message_id,
                recalled_at=first_time + timedelta(seconds=1),
                recalled_by_id="operator-2",
            )
        )
        await self.repository.save_incoming(message)

        self.assertIsNone(
            await self.repository.get_active(
                scope=self.scope,
                message_id=stored.message_id,
            )
        )
        async with self.runtime.session_factory() as session:
            archived_row = await session.scalar(
                select(GroupMessageRow).where(
                    GroupMessageRow.bot_id == self.bot_id,
                    GroupMessageRow.group_id == self.scope.group_id,
                    GroupMessageRow.message_id == "recalled",
                )
            )
        self.assertIsNotNone(archived_row)
        if archived_row is not None:
            self.assertEqual(archived_row.recalled_at, first_time)
            self.assertEqual(archived_row.recalled_by_id, "operator-1")
            self.assertEqual(
                archived_row.segments[0],
                {"type": "text", "data": {"text": "需要保留的原文"}},
            )

    async def test_rich_sent_record_wins_in_both_echo_orders(self) -> None:
        """出站原文无论早于还是晚于稀疏 echo，都是内容权威来源。"""
        for message_id, record_first in (
            ("record-before-echo", True),
            ("echo-before-record", False),
        ):
            rich_segments: list[MessageSegment] = [
                Text.new("完整出站原文"),
                Image.new("complete.png", url="https://example.invalid/complete.png"),
            ]
            echo = self._message(
                message_id=message_id,
                segments=[Image.new("sparse-echo.png")],
                sender_id=self.bot_id,
            ).model_copy(
                update={
                    "post_type": "message_sent",
                    "sender": Sender(
                        user_id=self.bot_id,
                        nickname=f"真实机器人-{message_id}",
                    ),
                }
            )
            if record_first:
                await self.repository.record_sent(
                    scope=self.scope,
                    message_id=message_id,
                    segments=rich_segments,
                )
                await self.repository.save_incoming(echo)
            else:
                await self.repository.save_incoming(echo)
                await self.repository.record_sent(
                    scope=self.scope,
                    message_id=message_id,
                    segments=rich_segments,
                )
            stored = await self.repository.get_active(
                scope=self.scope,
                message_id=message_id,
            )
            self.assertIsNotNone(stored)
            if stored is not None:
                self.assertEqual(stored.segments, tuple(rich_segments))
                self.assertEqual(
                    stored.sender_name,
                    f"真实机器人-{message_id}",
                )
                self.assertEqual(
                    [image.segment_index for image in stored.images],
                    [1],
                )

        recalled_at = datetime(2026, 8, 16, 10, 2, tzinfo=UTC)
        self.assertTrue(
            await self.repository.archive(
                scope=self.scope,
                message_id="record-before-echo",
                recalled_at=recalled_at,
                recalled_by_id="operator",
            )
        )
        await self.repository.save_incoming(
            self._message(
                message_id="record-before-echo",
                segments=[Text.new("再次迟到的 echo")],
                sender_id=self.bot_id,
            ).model_copy(
                update={
                    "post_type": "message_sent",
                    "sender": Sender(user_id=self.bot_id, nickname="机器人"),
                }
            )
        )
        async with self.runtime.session_factory() as session:
            row = await session.scalar(
                select(GroupMessageRow).where(
                    GroupMessageRow.bot_id == self.bot_id,
                    GroupMessageRow.group_id == self.scope.group_id,
                    GroupMessageRow.message_id == "record-before-echo",
                )
            )
        self.assertIsNotNone(row)
        if row is not None:
            self.assertEqual(row.recalled_at, recalled_at)
            self.assertEqual(
                row.sender_name,
                "真实机器人-record-before-echo",
            )

    async def test_late_echo_only_enriches_existing_inline_image_task(self) -> None:
        """迟到 echo 可补充已租用任务的来源，并供下一次尝试使用。"""
        message_id = "inline-then-echo"
        write_result = await self.repository.record_sent(
            scope=self.scope,
            message_id=message_id,
            segments=[Image.new("base64://YWJj")],
        )
        self.assertIsNone(write_result)
        leased_task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        leased_row_before_echo = await self._image_row(task_id=leased_task.task_id)
        echo = self._message(
            message_id=message_id,
            sender_id=self.bot_id,
            segments=[
                Image.new(
                    "qq-image.dat",
                    file_id="qq-file-id",
                    url="https://example.invalid/qq-image",
                )
            ],
        ).model_copy(
            update={
                "post_type": "message_sent",
                "sender": Sender(user_id=self.bot_id, nickname="机器人"),
            }
        )
        await self.repository.save_incoming(echo)

        leased_row_after_echo = await self._image_row(task_id=leased_task.task_id)
        self.assertEqual(leased_row_after_echo.status, "leased")
        self.assertEqual(leased_row_after_echo.attempt_count, 1)
        self.assertEqual(leased_row_after_echo.lease_token, leased_task.lease_token)
        self.assertEqual(
            leased_row_after_echo.leased_until,
            leased_row_before_echo.leased_until,
        )
        self.assertEqual(
            leased_row_after_echo.next_attempt_at,
            leased_row_before_echo.next_attempt_at,
        )
        self.assertEqual(leased_row_after_echo.source_file, "qq-image.dat")
        self.assertEqual(leased_row_after_echo.file_id, "qq-file-id")
        self.assertTrue(
            await self.repository.fail(
                task_id=leased_task.task_id,
                lease_token=leased_task.lease_token,
                retry_at=datetime.now(UTC),
            )
        )

        stored = await self.repository.get_active(
            scope=self.scope,
            message_id=message_id,
        )
        self.assertIsNotNone(stored)
        if stored is not None and isinstance(stored.segments[0], Image):
            self.assertEqual(stored.segments[0].data.file, "qq-image.dat")
            self.assertNotIn("base64://", stored.segments[0].data.file)
        task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        self.assertEqual(task.attempt_number, 2)
        self.assertEqual(task.file, "qq-image.dat")
        self.assertEqual(task.file_id, "qq-file-id")
        future_retry = datetime.now(UTC) + timedelta(hours=1)
        self.assertTrue(
            await self.repository.fail(
                task_id=task.task_id,
                lease_token=task.lease_token,
                retry_at=future_retry,
            )
        )

        await self.repository.save_incoming(echo)
        repeated_echo_row = await self._image_row(task_id=task.task_id)
        self.assertEqual(repeated_echo_row.status, "retry")
        self.assertEqual(repeated_echo_row.attempt_count, 2)
        self.assertEqual(repeated_echo_row.next_attempt_at, future_retry)
        self.assertEqual(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            ),
            [],
        )

        enriched_echo = echo.model_copy(deep=True)
        enriched_segment = enriched_echo.message[0]
        self.assertIsInstance(enriched_segment, Image)
        if isinstance(enriched_segment, Image):
            enriched_segment.data.file_id = "qq-file-id-2"
        await self.repository.save_incoming(enriched_echo)
        enriched_task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        self.assertEqual(enriched_task.attempt_number, 3)
        self.assertEqual(enriched_task.file_id, "qq-file-id-2")

    async def test_image_leases_are_bot_scoped_retryable_and_rehydrate_path(self) -> None:
        """图片 worker 只认领当前 bot，并用存储键重组路径。"""
        await self.repository.save_incoming(
            self._message(
                message_id="image",
                segments=[
                    Image.new(
                        "source.png",
                        file_id="file-id",
                        path="C:/temporary/source.png",
                        url="https://example.invalid/source.png",
                    )
                ],
            )
        )
        stored = await self.repository.get_active(
            scope=self.scope,
            message_id="image",
        )
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("图片消息写入后应该可读")
        other_scope = GroupDataScope(bot_id=f"{self.bot_id}-other", group_id="group-1")
        other_message = self._message(
            message_id="other-image",
            segments=[Image.new("other.png")],
        ).model_copy(
            update={"self_id": other_scope.bot_id, "group_id": other_scope.group_id}
        )
        await self.repository.save_incoming(other_message)

        tasks = await self.repository.claim_ready(
            bot_id=self.bot_id,
            limit=10,
            lease_seconds=30,
        )
        self.assertEqual(len(tasks), 1)
        first_task = tasks[0]
        self.assertEqual(first_task.attempt_number, 1)
        self.assertEqual(first_task.path, "C:/temporary/source.png")
        retry_at = datetime.now(UTC)
        self.assertTrue(
            await self.repository.fail(
                task_id=first_task.task_id,
                lease_token=first_task.lease_token,
                retry_at=retry_at,
            )
        )
        retried = await self.repository.claim_ready(
            bot_id=self.bot_id,
            limit=1,
            lease_seconds=30,
        )
        self.assertEqual(retried[0].attempt_number, 2)
        self.assertTrue(
            await self.repository.complete(
                task_id=retried[0].task_id,
                lease_token=retried[0].lease_token,
                image=StoredImage(
                    storage_key="ab/content.png",
                    mime_type="image/png",
                    size_bytes=12,
                ),
            )
        )
        hydrated = await self.repository.get_active(
            scope=self.scope,
            message_id=stored.message_id,
        )
        self.assertIsNotNone(hydrated)
        if hydrated is None:
            self.fail("图片消息应该可读")
        segment = hydrated.segments[0]
        self.assertIsInstance(segment, Image)
        if isinstance(segment, Image):
            self.assertEqual(segment.data.path, str(self.image_root / "ab/content.png"))
        async with self.runtime.session_factory() as session:
            image_row = await session.scalar(
                select(GroupMessageImageRow).where(
                    GroupMessageImageRow.id == retried[0].task_id
                )
            )
        self.assertIsNotNone(image_row)
        if image_row is not None:
            self.assertIsNone(image_row.source_path)
        other_tasks = await self.repository.claim_ready(
            bot_id=other_scope.bot_id,
            limit=10,
            lease_seconds=30,
        )
        self.assertEqual(len(other_tasks), 1)

    async def test_permanent_failure_clears_only_temporary_source_path(
        self,
    ) -> None:
        """第四次显式失败和过期租约终止时都清除临时绝对路径。"""
        await self.repository.save_incoming(
            self._message(
                message_id="fourth-failure",
                segments=[
                    Image.new(
                        "source.png",
                        file_id="file-id",
                        path="C:/temporary/source.png",
                        url="https://example.invalid/source.png",
                    )
                ],
            )
        )
        last_task_id = 0
        for expected_attempt in range(1, 5):
            task = (
                await self.repository.claim_ready(
                    bot_id=self.bot_id,
                    limit=1,
                    lease_seconds=30,
                )
            )[0]
            last_task_id = task.task_id
            self.assertEqual(task.attempt_number, expected_attempt)
            self.assertTrue(
                await self.repository.fail(
                    task_id=task.task_id,
                    lease_token=task.lease_token,
                    retry_at=(
                        datetime.now(UTC) if expected_attempt < 4 else None
                    ),
                )
            )
        failed_row = await self._image_row(task_id=last_task_id)
        self.assertEqual(failed_row.status, "failed")
        self.assertEqual(failed_row.attempt_count, 4)
        self.assertIsNone(failed_row.source_path)
        self.assertEqual(failed_row.source_file, "source.png")
        self.assertEqual(
            failed_row.source_url,
            "https://example.invalid/source.png",
        )
        self.assertEqual(failed_row.file_id, "file-id")

        await self.repository.save_incoming(
            self._message(
                message_id="expired-fourth-lease",
                segments=[
                    Image.new(
                        "expired.png",
                        file_id="expired-file-id",
                        path="C:/temporary/expired.png",
                        url="https://example.invalid/expired.png",
                    )
                ],
            )
        )
        expired_task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        async with self.runtime.session_factory() as session, session.begin():
            _ = await session.execute(
                update(GroupMessageImageRow)
                .where(GroupMessageImageRow.id == expired_task.task_id)
                .values(
                    attempt_count=4,
                    leased_until=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        self.assertEqual(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            ),
            [],
        )
        expired_row = await self._image_row(task_id=expired_task.task_id)
        self.assertEqual(expired_row.status, "failed")
        self.assertEqual(expired_row.attempt_count, 4)
        self.assertIsNone(expired_row.source_path)
        self.assertEqual(expired_row.source_file, "expired.png")
        self.assertEqual(
            expired_row.source_url,
            "https://example.invalid/expired.png",
        )
        self.assertEqual(expired_row.file_id, "expired-file-id")

    async def test_duplicate_image_event_preserves_retry_budget_and_terminal_states(
        self,
    ) -> None:
        """普通重复事件不改图片来源、重试预算或终态。"""
        message_id = "image-retry-state"
        original = self._message(
            message_id=message_id,
            segments=[
                Image.new(
                    "source.png",
                    file_id="file-id-1",
                    path="C:/temporary/source.png",
                    url="https://example.invalid/source-1.png",
                )
            ],
        )
        await self.repository.save_incoming(original)
        first_task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        future_retry = datetime.now(UTC) + timedelta(hours=1)
        self.assertTrue(
            await self.repository.fail(
                task_id=first_task.task_id,
                lease_token=first_task.lease_token,
                retry_at=future_retry,
            )
        )

        await self.repository.save_incoming(original)
        retry_row = await self._image_row(task_id=first_task.task_id)
        self.assertEqual(retry_row.status, "retry")
        self.assertEqual(retry_row.attempt_count, 1)
        self.assertEqual(retry_row.next_attempt_at, future_retry)
        self.assertEqual(retry_row.source_path, "C:/temporary/source.png")
        self.assertEqual(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            ),
            [],
        )

        changed_url = original.model_copy(
            update={
                "message": [
                    Image.new(
                        "source.png",
                        file_id="file-id-1",
                        path="C:/temporary/source.png",
                        url="https://example.invalid/source-2.png",
                    )
                ]
            }
        )
        await self.repository.save_incoming(changed_url)
        unchanged_retry_row = await self._image_row(task_id=first_task.task_id)
        self.assertEqual(
            unchanged_retry_row.source_url,
            "https://example.invalid/source-1.png",
        )
        self.assertEqual(unchanged_retry_row.next_attempt_at, future_retry)
        self.assertEqual(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            ),
            [],
        )
        async with self.runtime.session_factory() as session, session.begin():
            _ = await session.execute(
                update(GroupMessageImageRow)
                .where(GroupMessageImageRow.id == first_task.task_id)
                .values(next_attempt_at=datetime.now(UTC))
            )
        second_task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        self.assertEqual(second_task.attempt_number, 2)
        self.assertEqual(second_task.url, "https://example.invalid/source-1.png")
        self.assertTrue(
            await self.repository.fail(
                task_id=second_task.task_id,
                lease_token=second_task.lease_token,
                retry_at=None,
            )
        )

        await self.repository.save_incoming(changed_url)
        failed_row = await self._image_row(task_id=first_task.task_id)
        self.assertEqual(failed_row.status, "failed")
        self.assertEqual(failed_row.attempt_count, 2)
        self.assertIsNone(failed_row.source_path)
        self.assertEqual(failed_row.source_file, "source.png")
        self.assertEqual(
            failed_row.source_url,
            "https://example.invalid/source-1.png",
        )
        self.assertEqual(failed_row.file_id, "file-id-1")
        self.assertEqual(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            ),
            [],
        )

        changed_file_id = changed_url.model_copy(
            update={
                "message": [
                    Image.new(
                        "source.png",
                        file_id="file-id-2",
                        path="C:/temporary/source.png",
                        url="https://example.invalid/source-2.png",
                    )
                ]
            }
        )
        await self.repository.save_incoming(changed_file_id)
        changed_failed_row = await self._image_row(task_id=first_task.task_id)
        self.assertEqual(changed_failed_row.status, "failed")
        self.assertEqual(changed_failed_row.attempt_count, 2)
        self.assertEqual(changed_failed_row.file_id, "file-id-1")
        self.assertEqual(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            ),
            [],
        )

        stored_message = self._message(
            message_id="image-stored-terminal",
            segments=[
                Image.new(
                    "stored.png",
                    file_id="stored-file-id-1",
                    path="C:/temporary/stored.png",
                    url="https://example.invalid/stored-1.png",
                )
            ],
        )
        await self.repository.save_incoming(stored_message)
        stored_task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        self.assertTrue(
            await self.repository.complete(
                task_id=stored_task.task_id,
                lease_token=stored_task.lease_token,
                image=StoredImage(
                    storage_key="cd/content.png",
                    mime_type="image/png",
                    size_bytes=12,
                ),
            )
        )

        changed_after_store = stored_message.model_copy(
            update={
                "message": [
                    Image.new(
                        "stored.png",
                        file_id="stored-file-id-2",
                        path="C:/temporary/stored-new.png",
                        url="https://example.invalid/stored-2.png",
                    )
                ]
            }
        )
        await self.repository.save_incoming(changed_after_store)
        stored_row = await self._image_row(task_id=stored_task.task_id)
        self.assertEqual(stored_row.status, "stored")
        self.assertEqual(stored_row.storage_key, "cd/content.png")
        self.assertEqual(stored_row.file_id, "stored-file-id-1")
        self.assertEqual(
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            ),
            [],
        )

    async def test_video_is_metadata_only_and_does_not_create_download_task(self) -> None:
        """视频段保留可用信息，但绝对路径不入库且不创建任务。"""
        await self.repository.save_incoming(
            self._message(
                message_id="video",
                segments=[
                    Video.new(
                        "clip.mp4",
                        path="C:/temporary/clip.mp4",
                        url="https://example.invalid/clip.mp4",
                    )
                ],
            )
        )
        stored = await self.repository.get_active(
            scope=self.scope,
            message_id="video",
        )
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("视频消息写入后应该可读")
        self.assertEqual(stored.segments[0], Video.new("clip.mp4", url="https://example.invalid/clip.mp4"))
        tasks = await self.repository.claim_ready(
            bot_id=self.bot_id,
            limit=10,
            lease_seconds=30,
        )
        self.assertEqual(tasks, [])

    async def test_absolute_image_file_is_only_a_temporary_task_source(self) -> None:
        """绝对 file 路径不进 segments 或长期图片 DTO。"""
        await self.repository.save_incoming(
            self._message(
                message_id="absolute-image",
                segments=[Image.new("C:/temporary/photo.png")],
            )
        )
        stored = await self.repository.get_active(
            scope=self.scope,
            message_id="absolute-image",
        )
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("绝对路径图片写入后应该可读")
        segment = stored.segments[0]
        self.assertIsInstance(segment, Image)
        if isinstance(segment, Image):
            self.assertEqual(segment.data.file, "photo.png")
            self.assertIsNone(segment.data.path)
        self.assertIsNone(stored.images[0].source_file)
        task = (
            await self.repository.claim_ready(
                bot_id=self.bot_id,
                limit=1,
                lease_seconds=30,
            )
        )[0]
        self.assertEqual(task.path, "C:/temporary/photo.png")

    async def test_nested_forward_media_is_sanitized_without_creating_tasks(
        self,
    ) -> None:
        """嵌套转发只清理媒体来源，不把内联字节写库或递归建任务。"""
        inline_url = "DATA:image/png;base64,aW1hZ2UtYnl0ZXM="
        forward_content = [
            {
                "path": "business-wrapper-path",
                "message": [
                    {
                        "type": "image",
                        "data": {
                            "file": "BASE64://aW1hZ2UtYnl0ZXM=",
                            "path": "DATA:image/png;base64,cGF0aC1ieXRlcw==",
                            "url": inline_url,
                            "file_id": "nested-file-id",
                        },
                    },
                    {
                        "type": "video",
                        "data": {
                            "file": "C:/temporary/nested.mp4",
                            "path": "C:/temporary/nested.mp4",
                            "url": "https://example.invalid/nested.mp4",
                        },
                    },
                    {
                        "type": "share",
                        "data": {
                            "url": "https://example.invalid/article",
                            "image": "DATA:image/png;base64,c2hhcmUtaW1hZ2U=",
                        },
                    },
                    {
                        "type": "music",
                        "data": {
                            "type": "custom",
                            "url": "BASE64://bXVzaWMtdXJs",
                            "audio": "DATA:audio/mpeg;base64,bXVzaWMtYXVkaW8=",
                            "image": "data:image/png;base64,bXVzaWMtaW1hZ2U=",
                        },
                    },
                    {
                        "type": "lightapp",
                        "data": {
                            "url": "DATA:image/png;base64,bGlnaHRhcHAtaW1hZ2U=",
                            "content": {
                                "url": "data:business-card-value",
                                "path": "business-lightapp-path",
                            },
                        },
                    },
                    {
                        "type": "custom-card",
                        "data": {
                            "path": "business-card-path",
                            "file": "business-card-file",
                        },
                    },
                ],
            }
        ]
        await self.repository.save_incoming(
            self._message(
                message_id="nested-forward-media",
                segments=[
                    Forward.new("forward-id", content=forward_content),
                    Image.new(
                        "BASE64://dG9wLWZpbGU=",
                        path="DATA:image/png;base64,dG9wLXBhdGg=",
                        url=inline_url,
                        file_id="top-inline-file-id",
                    ),
                ],
            )
        )

        stored = await self.repository.get_active(
            scope=self.scope,
            message_id="nested-forward-media",
        )
        self.assertIsNotNone(stored)
        if stored is None:
            self.fail("合并转发消息应该可读")
        forward = stored.segments[0]
        self.assertIsInstance(forward, Forward)
        if not isinstance(forward, Forward):
            self.fail("第一段应该是合并转发")
        content = forward.data.content
        self.assertIsInstance(content, list)
        if not isinstance(content, list) or not isinstance(content[0], dict):
            self.fail("合并转发内嵌内容结构不正确")
        wrapper = content[0]
        self.assertEqual(wrapper.get("path"), "business-wrapper-path")
        messages = wrapper.get("message")
        self.assertIsInstance(messages, list)
        if not isinstance(messages, list):
            self.fail("合并转发消息段列表缺失")
        nested_image = messages[0]
        nested_video = messages[1]
        nested_share = messages[2]
        nested_music = messages[3]
        nested_lightapp = messages[4]
        business_card = messages[5]
        if not isinstance(nested_image, dict):
            self.fail("合并转发图片段必须保持对象结构")
        if not isinstance(nested_video, dict):
            self.fail("合并转发视频段必须保持对象结构")
        if not isinstance(nested_share, dict):
            self.fail("合并转发分享段必须保持对象结构")
        if not isinstance(nested_music, dict):
            self.fail("合并转发音乐段必须保持对象结构")
        if not isinstance(nested_lightapp, dict):
            self.fail("合并转发 LightApp 段必须保持对象结构")
        if not isinstance(business_card, dict):
            self.fail("合并转发业务卡片必须保持对象结构")
        image_data = nested_image.get("data")
        video_data = nested_video.get("data")
        share_data = nested_share.get("data")
        music_data = nested_music.get("data")
        lightapp_data = nested_lightapp.get("data")
        card_data = business_card.get("data")
        self.assertEqual(
            image_data,
            {"file": "[inline-media]", "file_id": "nested-file-id"},
        )
        self.assertEqual(
            video_data,
            {
                "file": "nested.mp4",
                "url": "https://example.invalid/nested.mp4",
            },
        )
        self.assertEqual(
            share_data,
            {"url": "https://example.invalid/article"},
        )
        self.assertEqual(music_data, {"type": "custom"})
        self.assertEqual(
            lightapp_data,
            {
                "content": {
                    "url": "data:business-card-value",
                    "path": "business-lightapp-path",
                }
            },
        )
        self.assertEqual(
            card_data,
            {"path": "business-card-path", "file": "business-card-file"},
        )
        top_level_image = stored.segments[1]
        self.assertIsInstance(top_level_image, Image)
        if isinstance(top_level_image, Image):
            self.assertEqual(top_level_image.data.file, "[inline-media]")
            self.assertIsNone(top_level_image.data.path)
            self.assertIsNone(top_level_image.data.url)
        self.assertNotIn("aW1hZ2UtYnl0ZXM=", repr(stored.segments))

        tasks = await self.repository.claim_ready(
            bot_id=self.bot_id,
            limit=10,
            lease_seconds=30,
        )
        self.assertEqual(len(tasks), 1)
        self.assertIsNone(tasks[0].file)
        self.assertIsNone(tasks[0].path)
        self.assertIsNone(tasks[0].url)
        self.assertEqual(tasks[0].file_id, "top-inline-file-id")

    async def _image_row(self, *, task_id: int) -> GroupMessageImageRow:
        """按任务 ID 读取图片状态行。"""
        async with self.runtime.session_factory() as session:
            row = await session.get(GroupMessageImageRow, task_id)
        if row is None:
            self.fail(f"找不到图片任务 {task_id}")
        return row

    def _message(
        self,
        *,
        message_id: str,
        text: str = "text",
        occurred_at: datetime | None = None,
        sender_id: str = "sender",
        segments: list[MessageSegment] | None = None,
    ) -> GroupMessage:
        """构造当前用例数据范围内的 NapCat 群消息。"""
        actual_time = occurred_at or datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
        return GroupMessage(
            time=int(actual_time.timestamp()),
            self_id=self.scope.bot_id,
            post_type="message",
            message_type="group",
            user_id=sender_id,
            message_id=message_id,
            group_id=self.scope.group_id,
            group_name="测试群",
            message=segments or [Text.new(text)],
            sender=Sender(user_id=sender_id, nickname=f"name-{sender_id}"),
        )
