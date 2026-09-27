"""将图片任务扩展为图片与视频共用的媒体归档。

Revision ID: 202609280001
Revises: 202608160001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609280001"
down_revision: str | None = "202608160001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """原地保留全部图片记录，为新增视频使用同一租约与归档结构。"""
    op.rename_table("group_message_images", "group_message_media", schema="core")
    op.add_column(
        "group_message_media",
        sa.Column("media_type", sa.Text(), nullable=False, server_default="image"),
        schema="core",
    )
    op.alter_column("group_message_media", "media_type", server_default=None, schema="core")
    op.create_check_constraint(
        "ck_group_message_media_type", "group_message_media",
        "media_type IN ('image', 'video')", schema="core",
    )
    for old in (
        "group_message_images_pkey", "group_message_images_message_row_id_fkey",
        "uq_group_message_images_segment", "ck_group_message_images_status",
        "ck_group_message_images_attempts", "ck_group_message_images_size",
    ):
        new = old.replace("group_message_images", "group_message_media")
        op.execute(f'ALTER TABLE core.group_message_media RENAME CONSTRAINT "{old}" TO "{new}"')
    for suffix in ("ready", "expired_lease"):
        op.execute(
            f'ALTER INDEX core.ix_group_message_images_{suffix} '
            f'RENAME TO ix_group_message_media_{suffix}'
        )
    op.execute('ALTER SEQUENCE core.group_message_images_id_seq RENAME TO group_message_media_id_seq')


def downgrade() -> None:
    """旧应用无法表示视频记录，拒绝有损降级。"""
    raise RuntimeError("视频归档不能降级为仅支持图片的数据库结构")
