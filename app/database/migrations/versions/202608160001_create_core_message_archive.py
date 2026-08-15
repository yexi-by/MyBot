"""创建群消息和图片归档表。

Revision ID: 202608160001
Revises:
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608160001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 core schema 及当前有消费者的持久化表。"""
    op.execute('CREATE SCHEMA IF NOT EXISTS "core"')
    op.create_table(
        "group_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("bot_id", sa.Text(), nullable=False),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("sender_id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=True),
        sa.Column("sender_name", sa.Text(), nullable=False),
        sa.Column("sender_role", sa.Text(), nullable=True),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recalled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recalled_by_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "direction IN ('incoming', 'outgoing')",
            name="ck_group_messages_direction",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bot_id",
            "group_id",
            "message_id",
            name="uq_group_messages_identity",
        ),
        schema="core",
    )
    op.create_index(
        "ix_group_messages_active_recent",
        "group_messages",
        ["bot_id", "group_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
        unique=False,
        schema="core",
        postgresql_where=sa.text("recalled_at IS NULL"),
    )
    op.create_index(
        "ix_group_messages_active_sender_recent",
        "group_messages",
        [
            "bot_id",
            "group_id",
            "sender_id",
            sa.text("occurred_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
        schema="core",
        postgresql_where=sa.text("recalled_at IS NULL"),
    )
    op.create_table(
        "group_message_images",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("message_row_id", sa.BigInteger(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("file_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Text(), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_group_message_images_attempts"
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_group_message_images_size",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'stored', 'retry', 'failed')",
            name="ck_group_message_images_status",
        ),
        sa.ForeignKeyConstraint(
            ["message_row_id"],
            ["core.group_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_row_id",
            "segment_index",
            name="uq_group_message_images_segment",
        ),
        schema="core",
    )
    op.create_index(
        "ix_group_message_images_ready",
        "group_message_images",
        ["next_attempt_at", "id"],
        unique=False,
        schema="core",
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )
    op.create_index(
        "ix_group_message_images_expired_lease",
        "group_message_images",
        ["leased_until", "id"],
        unique=False,
        schema="core",
        postgresql_where=sa.text("status = 'leased'"),
    )


def downgrade() -> None:
    """按外键依赖顺序删除核心表，保留 schema 供版本表使用。"""
    op.drop_index(
        "ix_group_message_images_expired_lease",
        table_name="group_message_images",
        schema="core",
    )
    op.drop_index(
        "ix_group_message_images_ready",
        table_name="group_message_images",
        schema="core",
    )
    op.drop_table("group_message_images", schema="core")
    op.drop_index(
        "ix_group_messages_active_sender_recent",
        table_name="group_messages",
        schema="core",
    )
    op.drop_index(
        "ix_group_messages_active_recent",
        table_name="group_messages",
        schema="core",
    )
    op.drop_table("group_messages", schema="core")
