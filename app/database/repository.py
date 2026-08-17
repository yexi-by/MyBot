"""PostgreSQL 群消息仓库实现。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import cast
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import (
    ColumnElement,
    and_,
    case,
    delete,
    func,
    or_,
    select,
    true,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.models import (
    GroupMessage,
    Image,
    ImageArchiveTask,
    JsonObject,
    JsonValue,
    MessageSegment,
    StoredImage,
)

from .models import GroupMessageImageRow, GroupMessageRow
from .schemas import (
    GroupDataScope,
    ImageArchiveStatus,
    MessageCursor,
    MessageDirection,
    StoredGroupImage,
    StoredGroupMessage,
)

_SEGMENTS_ADAPTER = TypeAdapter(list[MessageSegment])
_MAX_IMAGE_ATTEMPTS = 4
_INLINE_PREFIXES = ("base64://", "data:")
_PATH_SEGMENT_TYPES = frozenset(("image", "record", "video"))
_FILE_SEGMENT_TYPES = frozenset(("image", "record", "video", "file"))
_INLINE_SOURCE_FIELDS_BY_SEGMENT_TYPE = {
    "image": ("url",),
    "record": ("url",),
    "video": ("url",),
    "share": ("url", "image"),
    "music": ("url", "audio", "image"),
    "mface": ("url",),
    "lightapp": ("url",),
}
_EMBEDDED_SEGMENT_TYPES = frozenset(("forward", "node"))
_FORWARD_CONTENT_KEYS = ("message", "content", "messages")


@dataclass(frozen=True, slots=True)
class _PreparedImage:
    """仅在消息事务内传递的图片任务来源。"""

    segment_index: int
    source_file: str | None
    source_url: str | None
    source_path: str | None
    file_id: str | None


class PostgreSQLMessageRepository:
    """实现群消息读写、撤回归档和图片任务租约。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        image_root: Path,
    ) -> None:
        """保留 session factory 和只用于重组已归档图片的根目录。"""
        self._session_factory: async_sessionmaker[AsyncSession] = session_factory
        self._image_root: Path = image_root

    async def get_active(
        self, *, scope: GroupDataScope, message_id: str
    ) -> StoredGroupMessage | None:
        """按复合身份读取未撤回消息。"""
        self._validate_message_id(message_id)
        async with self._session_factory() as session:
            row = await self._select_one(
                session=session,
                scope=scope,
                message_id=message_id,
                active_only=True,
            )
            return self._to_stored_message(row) if row is not None else None

    async def list_recent(
        self,
        *,
        scope: GroupDataScope,
        limit: int,
        before: MessageCursor | None = None,
        sender_id: str | None = None,
    ) -> list[StoredGroupMessage]:
        """按 (occurred_at, id) 倒序读取未撤回消息。"""
        self._validate_limit(limit)
        statement = self._active_select(scope=scope, sender_id=sender_id)
        if before is not None:
            statement = statement.where(
                tuple_(GroupMessageRow.occurred_at, GroupMessageRow.id)
                < (before.occurred_at, before.row_id)
            )
        statement = statement.order_by(
            GroupMessageRow.occurred_at.desc(), GroupMessageRow.id.desc()
        ).limit(limit)
        return await self._execute_message_list(statement=statement)

    async def list_between(
        self,
        *,
        scope: GroupDataScope,
        start: datetime,
        end: datetime,
        limit: int,
        before: MessageCursor | None = None,
        sender_id: str | None = None,
    ) -> list[StoredGroupMessage]:
        """按半开时间区间和稳定游标读取消息。"""
        self._validate_limit(limit)
        self._validate_datetime(start, name="start")
        self._validate_datetime(end, name="end")
        if start >= end:
            raise ValueError("start 必须早于 end")
        statement = self._active_select(scope=scope, sender_id=sender_id).where(
            GroupMessageRow.occurred_at >= start,
            GroupMessageRow.occurred_at < end,
        )
        if before is not None:
            statement = statement.where(
                tuple_(GroupMessageRow.occurred_at, GroupMessageRow.id)
                < (before.occurred_at, before.row_id)
            )
        statement = statement.order_by(
            GroupMessageRow.occurred_at.desc(), GroupMessageRow.id.desc()
        ).limit(limit)
        return await self._execute_message_list(statement=statement)

    async def list_around(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        before_count: int,
        after_count: int,
        sender_id: str | None = None,
    ) -> list[StoredGroupMessage]:
        """用一条消息查询读取锚点的前后文。"""
        self._validate_message_id(message_id)
        self._validate_count(before_count, name="before_count")
        self._validate_count(after_count, name="after_count")
        anchor = (
            select(
                GroupMessageRow.id.label("row_id"),
                GroupMessageRow.occurred_at.label("occurred_at"),
                GroupMessageRow.sender_id.label("sender_id"),
            )
            .where(
                *self._active_conditions(scope=scope),
                GroupMessageRow.message_id == message_id,
            )
            .cte("around_anchor")
        )
        candidate_conditions = self._active_conditions(scope=scope)
        if sender_id is not None:
            candidate_conditions.append(GroupMessageRow.sender_id == sender_id)

        previous_ids = (
            select(GroupMessageRow.id.label("row_id"))
            .join(anchor, true())
            .where(
                *candidate_conditions,
                tuple_(GroupMessageRow.occurred_at, GroupMessageRow.id)
                < tuple_(anchor.c.occurred_at, anchor.c.row_id),
            )
            .order_by(GroupMessageRow.occurred_at.desc(), GroupMessageRow.id.desc())
            .limit(before_count)
            .cte("around_previous")
        )
        following_ids = (
            select(GroupMessageRow.id.label("row_id"))
            .join(anchor, true())
            .where(
                *candidate_conditions,
                tuple_(GroupMessageRow.occurred_at, GroupMessageRow.id)
                > tuple_(anchor.c.occurred_at, anchor.c.row_id),
            )
            .order_by(GroupMessageRow.occurred_at.asc(), GroupMessageRow.id.asc())
            .limit(after_count)
            .cte("around_following")
        )
        anchor_id = select(anchor.c.row_id)
        if sender_id is not None:
            anchor_id = anchor_id.where(anchor.c.sender_id == sender_id)
        selected_ids = union_all(
            select(previous_ids.c.row_id),
            anchor_id,
            select(following_ids.c.row_id),
        ).cte("around_selected")
        statement = (
            select(GroupMessageRow)
            .join(selected_ids, GroupMessageRow.id == selected_ids.c.row_id)
            .options(selectinload(GroupMessageRow.images))
            .order_by(GroupMessageRow.occurred_at.asc(), GroupMessageRow.id.asc())
        )
        return await self._execute_message_list(statement=statement)

    async def save_incoming(self, message: GroupMessage) -> None:
        """幂等保存入站群消息，且不覆盖已有撤回证据。"""
        scope = GroupDataScope(bot_id=message.self_id, group_id=message.group_id)
        occurred_at = datetime.fromtimestamp(message.time, tz=UTC)
        direction: MessageDirection = (
            "outgoing" if message.post_type == "message_sent" else "incoming"
        )
        sender_name = message.sender.card or message.sender.nickname
        segments, images = self._prepare_segments(message.message)
        async with self._session_factory() as session, session.begin():
            if direction == "outgoing":
                row_id, echo_inserted = await self._insert_outgoing_echo(
                    session=session,
                    scope=scope,
                    message_id=message.message_id,
                    group_name=message.group_name,
                    sender_id=message.user_id,
                    sender_name=sender_name,
                    sender_role=message.sender.role,
                    occurred_at=occurred_at,
                    segments=segments,
                )
                if echo_inserted:
                    await self._sync_image_tasks(
                        session=session,
                        message_row_id=row_id,
                        images=images,
                        next_attempt_at=datetime.now(UTC),
                    )
                else:
                    await self._merge_echo_image_sources(
                        session=session,
                        message_row_id=row_id,
                        images=images,
                        ready_at=datetime.now(UTC),
                    )
                return

            row_id, inserted = await self._insert_incoming_message(
                session=session,
                scope=scope,
                message_id=message.message_id,
                group_name=message.group_name,
                sender_id=message.user_id,
                sender_name=sender_name,
                sender_role=message.sender.role,
                occurred_at=occurred_at,
                segments=segments,
            )
            if inserted:
                await self._sync_image_tasks(
                    session=session,
                    message_row_id=row_id,
                    images=images,
                    next_attempt_at=datetime.now(UTC),
                )

    async def record_sent(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        segments: Sequence[MessageSegment],
        occurred_at: datetime | None = None,
    ) -> None:
        """保存 NapCat 已成功发送的群消息，不覆盖先到 echo。"""
        self._validate_message_id(message_id)
        actual_time = occurred_at or datetime.now(UTC)
        self._validate_datetime(actual_time, name="occurred_at")
        stored_segments, images = self._prepare_segments(segments)
        async with self._session_factory() as session, session.begin():
            statement = insert(GroupMessageRow).values(
                bot_id=scope.bot_id,
                group_id=scope.group_id,
                message_id=message_id,
                sender_id=scope.bot_id,
                occurred_at=actual_time,
                direction="outgoing",
                group_name=None,
                sender_name="机器人",
                sender_role=None,
                segments=stored_segments,
            )
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=["bot_id", "group_id", "message_id"],
                set_={
                    "sender_id": excluded.sender_id,
                    "direction": excluded.direction,
                    "segments": excluded.segments,
                },
            ).returning(GroupMessageRow.id)
            row_id = await session.scalar(statement)
            if row_id is None:
                raise RuntimeError("出站消息 upsert 未返回行 ID")
            await self._sync_image_tasks(
                session=session,
                message_row_id=row_id,
                images=images,
                next_attempt_at=datetime.now(UTC),
            )

    async def archive(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        recalled_at: datetime,
        recalled_by_id: str,
    ) -> bool:
        """首次撤回时写入归档字段，后续重复事件不篡改证据。"""
        self._validate_message_id(message_id)
        self._validate_datetime(recalled_at, name="recalled_at")
        if recalled_by_id.strip() == "":
            raise ValueError("recalled_by_id 不能为空")
        async with self._session_factory() as session, session.begin():
            statement = (
                update(GroupMessageRow)
                .where(
                    GroupMessageRow.bot_id == scope.bot_id,
                    GroupMessageRow.group_id == scope.group_id,
                    GroupMessageRow.message_id == message_id,
                )
                .values(
                    recalled_at=func.coalesce(
                        GroupMessageRow.recalled_at, recalled_at
                    ),
                    recalled_by_id=func.coalesce(
                        GroupMessageRow.recalled_by_id, recalled_by_id
                    ),
                )
                .returning(GroupMessageRow.id)
            )
            return await session.scalar(statement) is not None

    async def claim_ready(
        self,
        *,
        bot_id: str,
        limit: int,
        lease_seconds: float,
    ) -> Sequence[ImageArchiveTask]:
        """原子认领就绪任务或已过期租约，并递增尝试次数。"""
        self._validate_limit(limit)
        if bot_id.strip() == "":
            raise ValueError("bot_id 不能为空")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        now = datetime.now(UTC)
        leased_until = now + timedelta(seconds=lease_seconds)
        tasks: list[ImageArchiveTask] = []
        async with self._session_factory() as session, session.begin():
            _ = await session.execute(
                update(GroupMessageImageRow)
                .where(
                    GroupMessageImageRow.message_row_id.in_(
                        select(GroupMessageRow.id).where(
                            GroupMessageRow.bot_id == bot_id
                        )
                    ),
                    GroupMessageImageRow.status == "leased",
                    GroupMessageImageRow.leased_until <= now,
                    GroupMessageImageRow.attempt_count >= _MAX_IMAGE_ATTEMPTS,
                )
                .values(
                    status="failed",
                    source_path=None,
                    lease_token=None,
                    leased_until=None,
                    next_attempt_at=None,
                )
            )
            ready = or_(
                and_(
                    GroupMessageImageRow.status.in_(("pending", "retry")),
                    or_(
                        GroupMessageImageRow.next_attempt_at.is_(None),
                        GroupMessageImageRow.next_attempt_at <= now,
                    ),
                ),
                and_(
                    GroupMessageImageRow.status == "leased",
                    GroupMessageImageRow.leased_until <= now,
                ),
            )
            statement = (
                select(GroupMessageImageRow)
                .join(
                    GroupMessageRow,
                    GroupMessageRow.id == GroupMessageImageRow.message_row_id,
                )
                .where(
                    GroupMessageRow.bot_id == bot_id,
                    ready,
                    GroupMessageImageRow.attempt_count < _MAX_IMAGE_ATTEMPTS,
                )
                .order_by(
                    GroupMessageImageRow.next_attempt_at.asc().nullsfirst(),
                    GroupMessageImageRow.id.asc(),
                )
                .limit(limit)
                .with_for_update(of=GroupMessageImageRow, skip_locked=True)
            )
            rows = list((await session.scalars(statement)).all())
            for row in rows:
                lease_token = uuid4().hex
                row.status = "leased"
                row.lease_token = lease_token
                row.leased_until = leased_until
                row.attempt_count += 1
                tasks.append(
                    ImageArchiveTask(
                        task_id=row.id,
                        lease_token=lease_token,
                        attempt_number=row.attempt_count,
                        label=f"群消息图片任务 {row.id}",
                        file=row.source_file,
                        file_id=row.file_id,
                        path=row.source_path,
                        url=row.source_url,
                    )
                )
        return tasks

    async def complete(
        self,
        *,
        task_id: int,
        lease_token: str,
        image: StoredImage,
    ) -> bool:
        """只允许当前租约持有者完成图片任务。"""
        self._validate_task_lease(task_id=task_id, lease_token=lease_token)
        async with self._session_factory() as session, session.begin():
            statement = (
                update(GroupMessageImageRow)
                .where(
                    GroupMessageImageRow.id == task_id,
                    GroupMessageImageRow.status == "leased",
                    GroupMessageImageRow.lease_token == lease_token,
                )
                .values(
                    status="stored",
                    storage_key=image.storage_key,
                    mime_type=image.mime_type,
                    size_bytes=image.size_bytes,
                    source_path=None,
                    lease_token=None,
                    leased_until=None,
                    next_attempt_at=None,
                )
                .returning(GroupMessageImageRow.id)
            )
            return await session.scalar(statement) is not None

    async def fail(
        self,
        *,
        task_id: int,
        lease_token: str,
        retry_at: datetime | None,
    ) -> bool:
        """记录可重试或最终失败。"""
        self._validate_task_lease(task_id=task_id, lease_token=lease_token)
        if retry_at is not None:
            self._validate_datetime(retry_at, name="retry_at")
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(GroupMessageImageRow)
                .where(
                    GroupMessageImageRow.id == task_id,
                    GroupMessageImageRow.status == "leased",
                    GroupMessageImageRow.lease_token == lease_token,
                )
                .with_for_update()
            )
            if row is None:
                return False
            should_retry = (
                retry_at is not None and row.attempt_count < _MAX_IMAGE_ATTEMPTS
            )
            row.status = "retry" if should_retry else "failed"
            row.next_attempt_at = retry_at if should_retry else None
            if not should_retry:
                row.source_path = None
            row.lease_token = None
            row.leased_until = None
            return True

    async def _execute_message_list(
        self, *, statement: Select[tuple[GroupMessageRow]]
    ) -> list[StoredGroupMessage]:
        """执行已构造的 ORM select 并转换为 DTO。"""
        async with self._session_factory() as session:
            rows = list((await session.scalars(statement)).all())
            return [self._to_stored_message(row) for row in rows]

    def _active_select(
        self, *, scope: GroupDataScope, sender_id: str | None
    ) -> Select[tuple[GroupMessageRow]]:
        """构造统一排除撤回消息的 select。"""
        statement = select(GroupMessageRow).options(
            selectinload(GroupMessageRow.images)
        ).where(*self._active_conditions(scope=scope))
        if sender_id is not None:
            statement = statement.where(GroupMessageRow.sender_id == sender_id)
        return statement

    def _active_conditions(
        self, *, scope: GroupDataScope
    ) -> list[ColumnElement[bool]]:
        """生成所有普通读取都必须带上的安全条件。"""
        return [
            GroupMessageRow.bot_id == scope.bot_id,
            GroupMessageRow.group_id == scope.group_id,
            GroupMessageRow.recalled_at.is_(None),
        ]

    async def _select_one(
        self,
        *,
        session: AsyncSession,
        scope: GroupDataScope,
        message_id: str,
        active_only: bool,
    ) -> GroupMessageRow | None:
        """在指定 session 中按复合唯一身份读取消息。"""
        statement = (
            select(GroupMessageRow)
            .options(selectinload(GroupMessageRow.images))
            .where(
                GroupMessageRow.bot_id == scope.bot_id,
                GroupMessageRow.group_id == scope.group_id,
                GroupMessageRow.message_id == message_id,
            )
        )
        if active_only:
            statement = statement.where(GroupMessageRow.recalled_at.is_(None))
        return await session.scalar(statement)

    async def _insert_incoming_message(
        self,
        *,
        session: AsyncSession,
        scope: GroupDataScope,
        message_id: str,
        group_name: str | None,
        sender_id: str,
        sender_name: str,
        sender_role: str | None,
        occurred_at: datetime,
        segments: list[JsonObject],
    ) -> tuple[int, bool]:
        """首次写入普通入站消息；重复事件只复用原行，不改写证据。"""
        self._validate_message_id(message_id)
        statement = insert(GroupMessageRow).values(
            bot_id=scope.bot_id,
            group_id=scope.group_id,
            message_id=message_id,
            sender_id=sender_id,
            occurred_at=occurred_at,
            direction="incoming",
            group_name=group_name,
            sender_name=sender_name,
            sender_role=sender_role,
            segments=segments,
        )
        inserted_id = await session.scalar(
            statement.on_conflict_do_nothing(
                index_elements=["bot_id", "group_id", "message_id"]
            ).returning(GroupMessageRow.id)
        )
        if inserted_id is not None:
            return inserted_id, True
        existing_id = await session.scalar(
            select(GroupMessageRow.id).where(
                GroupMessageRow.bot_id == scope.bot_id,
                GroupMessageRow.group_id == scope.group_id,
                GroupMessageRow.message_id == message_id,
            )
        )
        if existing_id is None:
            raise RuntimeError("入站群消息冲突后无法读取对应行")
        return existing_id, False

    async def _insert_outgoing_echo(
        self,
        *,
        session: AsyncSession,
        scope: GroupDataScope,
        message_id: str,
        group_name: str | None,
        sender_id: str,
        sender_name: str,
        sender_role: str | None,
        occurred_at: datetime,
        segments: list[JsonObject],
    ) -> tuple[int, bool]:
        """写入 echo 或补充元数据，避免稀疏 echo 覆盖出站原文。"""
        self._validate_message_id(message_id)
        insert_statement = (
            insert(GroupMessageRow)
            .values(
                bot_id=scope.bot_id,
                group_id=scope.group_id,
                message_id=message_id,
                sender_id=sender_id,
                occurred_at=occurred_at,
                direction="outgoing",
                group_name=group_name,
                sender_name=sender_name,
                sender_role=sender_role,
                segments=segments,
            )
            .on_conflict_do_nothing(
                index_elements=["bot_id", "group_id", "message_id"]
            )
            .returning(GroupMessageRow.id)
        )
        inserted_id = await session.scalar(insert_statement)
        if inserted_id is not None:
            return inserted_id, True
        resolved_sender_name = GroupMessageRow.sender_name
        if sender_name.strip() != "":
            resolved_sender_name = case(
                (
                    GroupMessageRow.sender_name.in_(("", "机器人")),
                    sender_name,
                ),
                else_=GroupMessageRow.sender_name,
            )
        update_statement = (
            update(GroupMessageRow)
            .where(
                GroupMessageRow.bot_id == scope.bot_id,
                GroupMessageRow.group_id == scope.group_id,
                GroupMessageRow.message_id == message_id,
            )
            .values(
                # echo 只补充 NapCat 提供的元数据，决不覆盖发送层原文。
                occurred_at=occurred_at,
                group_name=func.coalesce(GroupMessageRow.group_name, group_name),
                sender_name=resolved_sender_name,
                sender_role=func.coalesce(GroupMessageRow.sender_role, sender_role),
            )
            .returning(GroupMessageRow.id)
        )
        existing_id = await session.scalar(update_statement)
        if existing_id is None:
            raise RuntimeError("出站 echo 冲突后无法读取对应行")
        return existing_id, False

    async def _sync_image_tasks(
        self,
        *,
        session: AsyncSession,
        message_row_id: int,
        images: list[_PreparedImage],
        next_attempt_at: datetime,
    ) -> None:
        """使图片任务与权威消息段一致，不重置已完成任务。"""
        image_indexes = [image.segment_index for image in images]
        obsolete_condition = GroupMessageImageRow.message_row_id == message_row_id
        if image_indexes:
            obsolete_condition = and_(
                obsolete_condition,
                GroupMessageImageRow.segment_index.not_in(image_indexes),
            )
        _ = await session.execute(
            delete(GroupMessageImageRow).where(obsolete_condition)
        )
        for image in images:
            statement = insert(GroupMessageImageRow).values(
                message_row_id=message_row_id,
                segment_index=image.segment_index,
                source_file=image.source_file,
                source_url=image.source_url,
                source_path=image.source_path,
                file_id=image.file_id,
                status="pending",
                attempt_count=0,
                next_attempt_at=next_attempt_at,
            )
            excluded = statement.excluded
            source_changed = or_(
                and_(
                    excluded.source_file.is_not(None),
                    excluded.source_file.is_distinct_from(
                        GroupMessageImageRow.source_file
                    ),
                ),
                and_(
                    excluded.source_url.is_not(None),
                    excluded.source_url.is_distinct_from(
                        GroupMessageImageRow.source_url
                    ),
                ),
                and_(
                    excluded.source_path.is_not(None),
                    excluded.source_path.is_distinct_from(
                        GroupMessageImageRow.source_path
                    ),
                ),
                and_(
                    excluded.file_id.is_not(None),
                    excluded.file_id.is_distinct_from(GroupMessageImageRow.file_id),
                ),
            )
            _ = await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["message_row_id", "segment_index"],
                    set_={
                        "source_file": func.coalesce(
                            excluded.source_file,
                            GroupMessageImageRow.source_file,
                        ),
                        "source_url": func.coalesce(
                            excluded.source_url,
                            GroupMessageImageRow.source_url,
                        ),
                        "source_path": func.coalesce(
                            excluded.source_path,
                            GroupMessageImageRow.source_path,
                        ),
                        "file_id": func.coalesce(
                            excluded.file_id,
                            GroupMessageImageRow.file_id,
                        ),
                        "next_attempt_at": case(
                            (
                                and_(
                                    GroupMessageImageRow.status == "retry",
                                    source_changed,
                                ),
                                next_attempt_at,
                            ),
                            else_=GroupMessageImageRow.next_attempt_at,
                        ),
                    },
                    where=and_(
                        GroupMessageImageRow.status.in_(("pending", "retry", "leased")),
                        source_changed,
                    ),
                )
            )

    async def _merge_echo_image_sources(
        self,
        *,
        session: AsyncSession,
        message_row_id: int,
        images: list[_PreparedImage],
        ready_at: datetime,
    ) -> None:
        """只补充原文已有图片任务的来源，不让 echo 改变段结构。"""
        for image in images:
            source_changes: list[ColumnElement[bool]] = []
            if image.source_file is not None:
                source_changes.append(
                    GroupMessageImageRow.source_file.is_distinct_from(
                        image.source_file
                    )
                )
            if image.source_url is not None:
                source_changes.append(
                    GroupMessageImageRow.source_url.is_distinct_from(image.source_url)
                )
            if image.source_path is not None:
                source_changes.append(
                    GroupMessageImageRow.source_path.is_distinct_from(
                        image.source_path
                    )
                )
            if image.file_id is not None:
                source_changes.append(
                    GroupMessageImageRow.file_id.is_distinct_from(image.file_id)
                )
            if not source_changes:
                continue
            source_changed = or_(*source_changes)
            _ = await session.execute(
                update(GroupMessageImageRow)
                .where(
                    GroupMessageImageRow.message_row_id == message_row_id,
                    GroupMessageImageRow.segment_index == image.segment_index,
                    GroupMessageImageRow.status.in_(("pending", "retry", "leased")),
                    source_changed,
                )
                .values(
                    source_file=func.coalesce(
                        image.source_file,
                        GroupMessageImageRow.source_file,
                    ),
                    source_url=func.coalesce(
                        image.source_url,
                        GroupMessageImageRow.source_url,
                    ),
                    source_path=func.coalesce(
                        image.source_path,
                        GroupMessageImageRow.source_path,
                    ),
                    file_id=func.coalesce(
                        image.file_id,
                        GroupMessageImageRow.file_id,
                    ),
                    next_attempt_at=case(
                        (
                            and_(
                                GroupMessageImageRow.status == "retry",
                                source_changed,
                            ),
                            ready_at,
                        ),
                        else_=GroupMessageImageRow.next_attempt_at,
                    ),
                )
            )

    def _prepare_segments(
        self, segments: Sequence[MessageSegment]
    ) -> tuple[list[JsonObject], list[_PreparedImage]]:
        """递归清理消息段中的本地路径和内联字节，并提取顶层图片任务。"""
        stored_segments: list[JsonObject] = []
        images: list[_PreparedImage] = []
        for index, segment in enumerate(segments):
            raw = cast(
                JsonObject,
                segment.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
            self._sanitize_message_segment(segment=raw)
            stored_segments.append(raw)
            if not isinstance(segment, Image):
                continue
            raw_source_file = segment.data.file
            is_inline = self._is_inline_source(raw_source_file)
            is_absolute_path = self._is_absolute_path(raw_source_file)
            source_file = None if is_inline or is_absolute_path else raw_source_file
            source_path = segment.data.path
            if source_path is None and is_absolute_path:
                source_path = raw_source_file
            if source_path is not None and self._is_inline_source(source_path):
                source_path = None
            source_url = segment.data.url
            if source_url is not None and self._is_inline_source(source_url):
                source_url = None
            images.append(
                _PreparedImage(
                    segment_index=index,
                    source_file=source_file,
                    source_url=source_url,
                    source_path=source_path,
                    file_id=segment.data.file_id,
                )
            )
        return stored_segments, images

    def _sanitize_message_segment(self, *, segment: JsonObject) -> None:
        """只清理消息段已知的媒体来源字段，并递归处理转发节点内容。"""
        segment_type = segment.get("type")
        data_value = segment.get("data")
        if not isinstance(segment_type, str) or not isinstance(data_value, dict):
            return
        data = cast(JsonObject, data_value)
        if segment_type in _PATH_SEGMENT_TYPES:
            _ = data.pop("path", None)
        if segment_type in _FILE_SEGMENT_TYPES:
            raw_file = data.get("file")
            if isinstance(raw_file, str):
                if self._is_inline_source(raw_file):
                    data["file"] = "[inline-media]"
                elif self._is_absolute_path(raw_file):
                    data["file"] = self._source_basename(raw_file)
        for field_name in _INLINE_SOURCE_FIELDS_BY_SEGMENT_TYPE.get(segment_type, ()):
            raw_source = data.get(field_name)
            if isinstance(raw_source, str) and self._is_inline_source(raw_source):
                _ = data.pop(field_name, None)
        if segment_type in _EMBEDDED_SEGMENT_TYPES:
            self._sanitize_embedded_message_value(value=data.get("content"))

    def _sanitize_embedded_message_value(self, *, value: JsonValue) -> None:
        """沿合并转发的消息包装字段查找嵌套消息段，不触碰其他业务对象。"""
        if isinstance(value, list):
            for item in value:
                self._sanitize_embedded_message_value(value=item)
            return
        if not isinstance(value, dict):
            return
        payload = cast(JsonObject, value)
        if isinstance(payload.get("type"), str) and isinstance(
            payload.get("data"), dict
        ):
            self._sanitize_message_segment(segment=payload)
            return
        for key in _FORWARD_CONTENT_KEYS:
            nested_value = payload.get(key)
            if nested_value is not None:
                self._sanitize_embedded_message_value(value=nested_value)
        wrapper_data = payload.get("data")
        if not isinstance(wrapper_data, dict):
            return
        for key in _FORWARD_CONTENT_KEYS:
            nested_value = wrapper_data.get(key)
            if nested_value is not None:
                self._sanitize_embedded_message_value(value=nested_value)

    def _is_absolute_path(self, value: str) -> bool:
        """同时识别 Windows 和 POSIX 绝对路径，避免宿主系统差异。"""
        return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()

    def _is_inline_source(self, value: str) -> bool:
        """按 URI scheme 大小写不敏感地识别内联媒体字节。"""
        return value.casefold().startswith(_INLINE_PREFIXES)

    def _source_basename(self, value: str) -> str:
        """仅保留绝对路径的文件名供消息格式化使用。"""
        windows_name = PureWindowsPath(value).name
        posix_name = PurePosixPath(value).name
        return windows_name if len(windows_name) <= len(posix_name) else posix_name

    def _resolve_storage_path(self, *, storage_key: str) -> Path:
        """数据库被外部篡改时也不允许存储键逃出图片根目录。"""
        windows_path = PureWindowsPath(storage_key)
        posix_path = PurePosixPath(storage_key)
        if (
            windows_path.is_absolute()
            or posix_path.is_absolute()
            or ".." in windows_path.parts
            or ".." in posix_path.parts
        ):
            raise ValueError("storage_key 必须是图片根目录内的相对路径")
        return self._image_root / storage_key

    def _to_stored_message(self, row: GroupMessageRow) -> StoredGroupMessage:
        """将 ORM 行转换为不携带 session 的公共 DTO。"""
        parsed_segments = _SEGMENTS_ADAPTER.validate_python(row.segments)
        image_rows = tuple(row.images)
        for image_row in image_rows:
            if not 0 <= image_row.segment_index < len(parsed_segments):
                raise ValueError(
                    f"图片任务 {image_row.id} 的段序号超出消息范围"
                )
            segment = parsed_segments[image_row.segment_index]
            if not isinstance(segment, Image):
                raise ValueError(
                    f"图片任务 {image_row.id} 指向的消息段不是图片"
                )
            segment.data.file_id = segment.data.file_id or image_row.file_id
            segment.data.url = segment.data.url or image_row.source_url
            if (
                segment.data.file == "[inline-media]"
                and image_row.source_file is not None
            ):
                segment.data.file = image_row.source_file
            if image_row.status == "stored" and image_row.storage_key is not None:
                segment.data.path = str(
                    self._resolve_storage_path(storage_key=image_row.storage_key)
                )
        return StoredGroupMessage(
            row_id=row.id,
            scope=GroupDataScope(bot_id=row.bot_id, group_id=row.group_id),
            message_id=row.message_id,
            group_name=row.group_name,
            sender_id=row.sender_id,
            sender_name=row.sender_name,
            sender_role=row.sender_role,
            occurred_at=row.occurred_at,
            direction=cast(MessageDirection, row.direction),
            segments=tuple(parsed_segments),
            images=tuple(self._to_stored_image(item) for item in image_rows),
        )

    def _to_stored_image(self, row: GroupMessageImageRow) -> StoredGroupImage:
        """转换长期图片事实，故意不暴露临时 source_path。"""
        return StoredGroupImage(
            row_id=row.id,
            segment_index=row.segment_index,
            source_file=row.source_file,
            source_url=row.source_url,
            file_id=row.file_id,
            status=cast(ImageArchiveStatus, row.status),
            storage_key=row.storage_key,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
        )

    def _validate_limit(self, limit: int) -> None:
        """拒绝无界或无意义的数量。"""
        if limit < 1:
            raise ValueError("limit 必须大于等于 1")

    def _validate_count(self, count: int, *, name: str) -> None:
        """校验 around 查询前后数量。"""
        if count < 0:
            raise ValueError(f"{name} 不能小于 0")

    def _validate_datetime(self, value: datetime, *, name: str) -> None:
        """防止无时区时间在 PostgreSQL 边界产生歧义。"""
        if value.tzinfo is None:
            raise ValueError(f"{name} 必须带时区")

    def _validate_message_id(self, message_id: str) -> None:
        """消息 ID 不能为空。"""
        if message_id.strip() == "":
            raise ValueError("message_id 不能为空")

    def _validate_task_lease(self, *, task_id: int, lease_token: str) -> None:
        """校验图片任务租约身份。"""
        if task_id < 1:
            raise ValueError("task_id 必须大于等于 1")
        if lease_token.strip() == "":
            raise ValueError("lease_token 不能为空")
