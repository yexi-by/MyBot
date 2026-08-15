"""PostgreSQL 核心持久化表。"""

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models import JsonObject

CORE_SCHEMA: Final[str] = "core"
CORE_VERSION_TABLE: Final[str] = "alembic_version"


class DatabaseBase(DeclarativeBase):
    """SQLAlchemy 声明式模型基类。"""


class GroupMessageRow(DatabaseBase):
    """入站和出站群消息的核心记录。"""

    __tablename__ = "group_messages"
    __table_args__ = (
        UniqueConstraint(
            "bot_id", "group_id", "message_id", name="uq_group_messages_identity"
        ),
        CheckConstraint(
            "direction IN ('incoming', 'outgoing')",
            name="ck_group_messages_direction",
        ),
        Index(
            "ix_group_messages_active_recent",
            "bot_id",
            "group_id",
            text("occurred_at DESC"),
            text("id DESC"),
            postgresql_where=text("recalled_at IS NULL"),
        ),
        Index(
            "ix_group_messages_active_sender_recent",
            "bot_id",
            "group_id",
            "sender_id",
            text("occurred_at DESC"),
            text("id DESC"),
            postgresql_where=text("recalled_at IS NULL"),
        ),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    bot_id: Mapped[str] = mapped_column(Text, nullable=False)
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    sender_id: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    group_name: Mapped[str | None] = mapped_column(Text)
    sender_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender_role: Mapped[str | None] = mapped_column(Text)
    segments: Mapped[list[JsonObject]] = mapped_column(JSONB, nullable=False)
    recalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recalled_by_id: Mapped[str | None] = mapped_column(Text)
    images: Mapped[list["GroupMessageImageRow"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="GroupMessageImageRow.segment_index",
    )


class GroupMessageImageRow(DatabaseBase):
    """群消息顶层图片段的存储任务和结果。"""

    __tablename__ = "group_message_images"
    __table_args__ = (
        UniqueConstraint(
            "message_row_id",
            "segment_index",
            name="uq_group_message_images_segment",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'stored', 'retry', 'failed')",
            name="ck_group_message_images_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_group_message_images_attempts"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_group_message_images_size",
        ),
        Index(
            "ix_group_message_images_ready",
            "next_attempt_at",
            "id",
            postgresql_where=text("status IN ('pending', 'retry')"),
        ),
        Index(
            "ix_group_message_images_expired_lease",
            "leased_until",
            "id",
            postgresql_where=text("status = 'leased'"),
        ),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    message_row_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{CORE_SCHEMA}.group_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_file: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    storage_key: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(Text)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[GroupMessageRow] = relationship(back_populates="images")
