"""NapCat 入站事件在分发前的 PostgreSQL 持久化测试。"""

import unittest
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, patch

from app.core.server import EventPersistenceError, NapCatServer
from app.database import GroupDataScope, PostgreSQLMessageRepository
from app.models import GroupMessage, GroupRecallNoticeEvent, Sender, Text


@dataclass(frozen=True, slots=True)
class RecallCall:
    """一次撤回归档调用。"""

    scope: GroupDataScope
    message_id: str
    recalled_at: datetime
    recalled_by_id: str


class FakeMessageRepository:
    """记录入站写入和撤回归档的仓库。"""

    def __init__(self) -> None:
        """初始化调用记录和可注入失败。"""
        self.saved_messages: list[GroupMessage] = []
        self.recall_calls: list[RecallCall] = []
        self.save_failures: list[Exception] = []
        self.archive_failures: list[Exception] = []
        self.archive_result = True

    async def save_incoming(self, message: GroupMessage) -> None:
        """记录入站群消息。"""
        self.saved_messages.append(message)
        if self.save_failures:
            raise self.save_failures.pop(0)

    async def archive(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
        recalled_at: datetime,
        recalled_by_id: str,
    ) -> bool:
        """记录撤回归档参数。"""
        self.recall_calls.append(
            RecallCall(
                scope=scope,
                message_id=message_id,
                recalled_at=recalled_at,
                recalled_by_id=recalled_by_id,
            )
        )
        if self.archive_failures:
            raise self.archive_failures.pop(0)
        return self.archive_result


class PersistenceHarness(NapCatServer):
    """为保护方法提供明确的测试入口。"""

    async def persist_event_for_test(
        self,
        *,
        event: GroupMessage | GroupRecallNoticeEvent,
        repository: PostgreSQLMessageRepository,
    ) -> None:
        """调用真实的事件持久化逻辑。"""
        await self._persist_event(event=event, repository=repository)

    async def persist_with_retry_for_test[ResultT](
        self,
        *,
        operation: Callable[[], Awaitable[ResultT]],
        event_name: str,
        event_model: str,
        message_id: str,
    ) -> ResultT:
        """调用真实的单次重试逻辑。"""
        return await self._persist_with_retry(
            operation=operation,
            event_name=event_name,
            event_model=event_model,
            message_id=message_id,
        )


def _group_message() -> GroupMessage:
    """创建一条最小群消息。"""
    return GroupMessage(
        time=1_777_132_901,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id="30000",
        group_id="40000",
        group_name="测试群",
        message=[Text.new("需要持久化")],
        raw_message="需要持久化",
        sender=Sender(user_id="20000", nickname="测试成员", role="member"),
    )


def _recall_event() -> GroupRecallNoticeEvent:
    """创建一条群撤回通知。"""
    return GroupRecallNoticeEvent(
        time=1_777_132_999,
        self_id="10000",
        post_type="notice",
        notice_type="group_recall",
        group_id="40000",
        user_id="20000",
        operator_id="21000",
        message_id="30000",
    )


def _server() -> PersistenceHarness:
    """创建只用于测试无状态持久化方法的实例。"""
    return object.__new__(PersistenceHarness)


def _repository(fake: FakeMessageRepository) -> PostgreSQLMessageRepository:
    """把窄 fake 适配到服务当前的具体仓库参数。"""
    return cast(PostgreSQLMessageRepository, fake)


class PostgreSQLServerPersistenceTest(unittest.IsolatedAsyncioTestCase):
    """验证入站群事件先写库，且失败不会伪装成功。"""

    async def test_group_message_is_saved(self) -> None:
        """群消息按原事件交给入站仓库。"""
        repository = FakeMessageRepository()
        event = _group_message()

        await _server().persist_event_for_test(
            event=event,
            repository=_repository(repository),
        )

        self.assertEqual(repository.saved_messages, [event])
        self.assertEqual(repository.recall_calls, [])

    async def test_group_recall_is_archived_with_evidence(self) -> None:
        """撤回时间、操作者与 bot/群范围全部传入归档接口。"""
        repository = FakeMessageRepository()
        event = _recall_event()

        await _server().persist_event_for_test(
            event=event,
            repository=_repository(repository),
        )

        self.assertEqual(
            repository.recall_calls,
            [
                RecallCall(
                    scope=GroupDataScope(bot_id="10000", group_id="40000"),
                    message_id="30000",
                    recalled_at=datetime.fromtimestamp(event.time, tz=UTC),
                    recalled_by_id="21000",
                )
            ],
        )
        self.assertEqual(repository.saved_messages, [])

    async def test_first_failure_retries_once_then_returns_real_result(self) -> None:
        """首次失败等待 250ms，第二次成功才返回结果。"""
        attempts = 0
        sleep_mock = AsyncMock()

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("暂时不可用")
            return "stored"

        with patch("app.core.server.asyncio.sleep", sleep_mock):
            result = await _server().persist_with_retry_for_test(
                operation=operation,
                event_name="database.test.persist",
                event_model="GroupMessage",
                message_id="30000",
            )

        self.assertEqual(result, "stored")
        self.assertEqual(attempts, 2)
        sleep_mock.assert_awaited_once_with(0.25)

    async def test_two_failures_raise_without_false_success(self) -> None:
        """连续失败抛出会话级异常，不返回伪造结果。"""
        attempts = 0
        successful_side_effects: list[str] = []

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            raise RuntimeError(f"第 {attempts} 次失败")

        with (
            patch("app.core.server.asyncio.sleep", AsyncMock()),
            self.assertRaises(EventPersistenceError),
        ):
            result = await _server().persist_with_retry_for_test(
                operation=operation,
                event_name="database.test.persist",
                event_model="GroupMessage",
                message_id="30000",
            )
            successful_side_effects.append(result)

        self.assertEqual(attempts, 2)
        self.assertEqual(successful_side_effects, [])
