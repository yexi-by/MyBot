"""PostgreSQL migration 和插件 session 基础集成测试。"""

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from app.database import (
    DatabaseMigrationStateError,
    DatabaseMigrator,
    MAX_PLUGIN_ID_LENGTH,
    PluginMigrationRegistry,
    PluginMigrationSpec,
    PluginSessionFactory,
    PostgreSQLRuntime,
    plugin_schema_name,
    validate_plugin_id,
)
from app.plugins.base import PluginMeta

TEST_DATABASE_ENV = "MYBOT_TEST_DATABASE_URL"


class DatabaseMigrationsTest(unittest.IsolatedAsyncioTestCase):
    """验证空 schema 升级、版本拒绝和插件 schema 绑定。"""

    async def asyncSetUp(self) -> None:
        """连接专用测试库。"""
        database_url = os.environ.get(TEST_DATABASE_ENV)
        if database_url is None:
            self.skipTest(f"未配置 {TEST_DATABASE_ENV}，跳过 PostgreSQL 集成测试")
        self.database_url = database_url
        self.migrator = DatabaseMigrator(database_url=database_url)
        self.runtime = PostgreSQLRuntime.create(database_url=database_url)

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
                text("SELECT to_regclass('core.group_message_images')")
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
        self.assertEqual(images_table, "core.group_message_images")
        self.assertIn("ix_group_messages_active_recent", indexes)
        self.assertIn("ix_group_messages_active_sender_recent", indexes)

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

    async def test_plugin_session_uses_bound_schema_and_new_transaction(self) -> None:
        """插件 repository 的事务只使用由 plugin_id 确定的 schema。"""
        plugin_id = f"test_{uuid4().hex}"
        schema = f"plugin_{plugin_id}"
        async with self.runtime.engine.begin() as connection:
            _ = await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            _ = await connection.execute(
                text(f'CREATE TABLE "{schema}".items (value integer NOT NULL)')
            )
        try:
            sessions = PluginSessionFactory(
                engine=self.runtime.engine,
                plugin_id=plugin_id,
            )
            async with sessions.transaction() as first_session:
                first_schema = await first_session.scalar(text("SELECT current_schema()"))
            async with sessions.transaction() as second_session:
                second_schema = await second_session.scalar(
                    text("SELECT current_schema()")
                )
            self.assertEqual(first_schema, schema)
            self.assertEqual(second_schema, schema)
            self.assertIsNot(first_session, second_session)
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                async with sessions.transaction() as failed_session:
                    _ = await failed_session.execute(
                        text("INSERT INTO items (value) VALUES (1)")
                    )
                    raise RuntimeError("rollback")
            async with sessions.transaction() as verification_session:
                item_count = await verification_session.scalar(
                    text("SELECT count(*) FROM items")
                )
            self.assertEqual(item_count, 0)
        finally:
            async with self.runtime.engine.begin() as connection:
                _ = await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

    async def test_runtime_applies_durability_and_statement_timeout(self) -> None:
        """每条连接都启用持久提交和配置的语句超时。"""
        runtime = PostgreSQLRuntime.create(
            database_url=self.database_url,
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

    async def test_real_plugin_migration_uses_independent_schema_and_version_table(
        self,
    ) -> None:
        """真实运行临时插件 migration，不与 core 共用 schema 或版本表。"""
        plugin_id = f"migration_test_{uuid4().hex}"
        schema = f"plugin_{plugin_id}"
        revision = "plugin000001"
        with tempfile.TemporaryDirectory() as directory:
            script_location = Path(directory)
            versions = script_location / "versions"
            versions.mkdir()
            (script_location / "env.py").write_text(
                "from app.database import run_plugin_migration_environment\n"
                "run_plugin_migration_environment()\n",
                encoding="utf-8",
            )
            (versions / "plugin000001_create_state.py").write_text(
                "from alembic import op\n"
                "import sqlalchemy as sa\n"
                f"revision = {revision!r}\n"
                "down_revision = None\n"
                "branch_labels = None\n"
                "depends_on = None\n"
                "def upgrade():\n"
                "    schema = op.get_context().config.attributes['plugin_schema']\n"
                "    op.create_table('state', "
                "sa.Column('id', sa.Integer(), primary_key=True), schema=schema)\n"
                "def downgrade():\n"
                "    schema = op.get_context().config.attributes['plugin_schema']\n"
                "    op.drop_table('state', schema=schema)\n",
                encoding="utf-8",
            )
            registry = PluginMigrationRegistry()
            registry.register(
                PluginMigrationSpec(
                    plugin_id=plugin_id,
                    script_location=script_location,
                )
            )
            migrator = DatabaseMigrator(
                database_url=self.database_url,
                plugin_registry=registry,
            )
            try:
                await migrator.upgrade_all()
                await migrator.assert_current()
                async with self.runtime.engine.connect() as connection:
                    state_table = await connection.scalar(
                        text("SELECT to_regclass(:table_name)"),
                        {"table_name": f"{schema}.state"},
                    )
                    plugin_revision = await connection.scalar(
                        text(f'SELECT version_num FROM "{schema}".alembic_version')
                    )
                    core_revision = await connection.scalar(
                        text("SELECT version_num FROM core.alembic_version")
                    )
                self.assertEqual(state_table, f"{schema}.state")
                self.assertEqual(plugin_revision, revision)
                self.assertNotEqual(plugin_revision, core_revision)
            finally:
                async with self.runtime.engine.begin() as connection:
                    _ = await connection.execute(
                        text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    )


class PluginMigrationRegistryTest(unittest.TestCase):
    """不连接数据库的插件 migration 注册契约测试。"""

    def test_registry_orders_plugins_and_rejects_duplicate_identity(self) -> None:
        """插件 migration 按 ASCII ID 排序且不允许重复。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "env.py").touch()
            (second / "env.py").touch()
            registry = PluginMigrationRegistry()
            beta = PluginMigrationSpec(plugin_id="beta", script_location=second)
            alpha = PluginMigrationSpec(plugin_id="alpha", script_location=first)
            registry.register(beta)
            registry.register(alpha)

            self.assertEqual(
                [spec.plugin_id for spec in registry.ordered_specs()],
                ["alpha", "beta"],
            )
            with self.assertRaises(ValueError):
                registry.register(alpha)

    def test_plugin_id_rejects_postgresql_identifier_truncation(self) -> None:
        """两个可能被 PostgreSQL 截成同一 schema 的长 ID 都必须被拒绝。"""
        maximum_id = "a" * MAX_PLUGIN_ID_LENGTH
        self.assertEqual(validate_plugin_id(maximum_id), maximum_id)
        self.assertEqual(
            plugin_schema_name(maximum_id),
            f"plugin_{maximum_id}",
        )

        shared_prefix = "a" * MAX_PLUGIN_ID_LENGTH
        for suffix in ("x", "y"):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError, "PostgreSQL 会截断 schema 名"):
                    _ = validate_plugin_id(f"{shared_prefix}{suffix}")

        with self.assertRaisesRegex(ValueError, "PostgreSQL 会截断 schema 名"):
            _ = PluginMeta(
                "TooLongPluginId",
                (object,),
                {
                    "__module__": __name__,
                    "name": "超长插件标识测试",
                    "plugin_id": f"{shared_prefix}z",
                    "consumers_count": 1,
                    "priority": 0,
                },
            )
