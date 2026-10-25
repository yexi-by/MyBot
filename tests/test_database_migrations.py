"""PostgreSQL 核心 migration 与连接持久性集成测试。"""

import asyncio
import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.database import (
    DatabaseMigrationStateError,
    DatabaseMigrator,
    PostgreSQLRuntime,
)

TEST_DATABASE_ENV = "MYBOT_TEST_DATABASE_URL"


class DatabaseMigrationsTest(unittest.IsolatedAsyncioTestCase):
    """验证核心 schema 升级、版本拒绝和连接持久性。"""

    async def asyncSetUp(self) -> None:
        """连接专用测试库。"""
        database_url = os.environ.get(TEST_DATABASE_ENV)
        if database_url is None:
            self.skipTest(f"未配置 {TEST_DATABASE_ENV}，跳过 PostgreSQL 集成测试")
        self.database_url = database_url
        self.migrator = DatabaseMigrator(database_url=database_url)
        self.runtime = PostgreSQLRuntime.create(
            database_url=database_url,
            pool_size=20,
            max_overflow=20,
            pool_timeout_seconds=2,
            statement_timeout_seconds=5,
        )

    async def asyncTearDown(self) -> None:
        """关闭测试连接池。"""
        runtime = getattr(self, "runtime", None)
        if isinstance(runtime, PostgreSQLRuntime):
            await runtime.dispose()

    async def test_empty_core_schema_upgrades_to_postgresql_18_head(self) -> None:
        """专用测试库从空 core schema 可一次升到 head。"""
        async with self.runtime.engine.begin() as connection:
            _ = await connection.execute(text('DROP SCHEMA IF EXISTS "core" CASCADE'))
        await self.migrator.upgrade_all()
        await self.migrator.assert_current()
        async with self.runtime.engine.connect() as connection:
            version = await connection.scalar(text("SHOW server_version_num"))
            messages_table = await connection.scalar(
                text("SELECT to_regclass('core.group_messages')")
            )
            images_table = await connection.scalar(
                text("SELECT to_regclass('core.group_message_media')")
            )
            indexes = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = 'core'"
                        )
                    )
                ).all()
            )
        self.assertIsInstance(version, str)
        if isinstance(version, str):
            self.assertEqual(int(version) // 10000, 18)
        self.assertEqual(messages_table, "core.group_messages")
        self.assertEqual(images_table, "core.group_message_media")
        self.assertIn("ix_group_messages_active_recent", indexes)
        self.assertIn("ix_group_messages_active_sender_recent", indexes)

    async def test_video_upgrade_preserves_existing_image_archive(self) -> None:
        """真实升级保留既有图片行、主键和文件存储键。"""
        async with self.runtime.engine.begin() as connection:
            _ = await connection.execute(text('DROP SCHEMA IF EXISTS "core" CASCADE'))
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url.replace("%", "%%"))
        await asyncio.to_thread(command.upgrade, config, "202608160001")
        async with self.runtime.engine.begin() as connection:
            _ = await connection.execute(text("""
                WITH message AS (
                    INSERT INTO core.group_messages
                    (bot_id, group_id, message_id, sender_id, occurred_at, direction, sender_name, segments)
                    VALUES ('migration-test', 'group', 'msg', 'sender', now(), 'incoming', 'test', '[]') RETURNING id
                )
                INSERT INTO core.group_message_images
                (message_row_id, segment_index, status, storage_key, mime_type, size_bytes, attempt_count)
                SELECT id, 0, 'stored', 'ab/cd/image.png', 'image/png', 123, 1 FROM message
            """))
            old_id = await connection.scalar(text("SELECT id FROM core.group_message_images"))
        await self.migrator.upgrade_all()
        await self.migrator.assert_current()
        async with self.runtime.engine.begin() as connection:
            row = (await connection.execute(text(
                "SELECT id, media_type, status, storage_key, mime_type, size_bytes FROM core.group_message_media"
            ))).one()
            self.assertEqual(tuple(row), (old_id, "image", "stored", "ab/cd/image.png", "image/png", 123))
            _ = await connection.execute(text("DELETE FROM core.group_messages WHERE bot_id='migration-test'"))

    async def test_wrong_revision_is_rejected_without_automatic_upgrade(self) -> None:
        """启动检查对错误 revision 报错，并且不自动改表。"""
        await self.migrator.upgrade_all()
        async with self.runtime.engine.begin() as connection:
            actual = await connection.scalar(
                text("SELECT version_num FROM core.alembic_version")
            )
            _ = await connection.execute(
                text("UPDATE core.alembic_version SET version_num = 'wrong-revision'")
            )
        try:
            with self.assertRaises(DatabaseMigrationStateError):
                await self.migrator.assert_current()
        finally:
            async with self.runtime.engine.begin() as connection:
                _ = await connection.execute(
                    text("UPDATE core.alembic_version SET version_num = :revision"),
                    {"revision": actual},
                )

    async def test_runtime_applies_durability_and_statement_timeout(self) -> None:
        """每条连接都启用持久提交和配置的语句超时。"""
        runtime = PostgreSQLRuntime.create(
            database_url=self.database_url,
            pool_size=20,
            max_overflow=20,
            pool_timeout_seconds=2,
            statement_timeout_seconds=1.25,
        )
        try:
            async with runtime.engine.connect() as connection:
                synchronous_commit = await connection.scalar(
                    text("SHOW synchronous_commit")
                )
                statement_timeout = await connection.scalar(
                    text("SHOW statement_timeout")
                )
            self.assertEqual(synchronous_commit, "on")
            self.assertEqual(statement_timeout, "1250ms")
        finally:
            await runtime.dispose()
