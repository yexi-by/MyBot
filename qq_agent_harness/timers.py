"""有界内存定时唤醒服务；不落盘持久化，进程重启后清空。"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from pydantic import Field

from .contracts import (
    ConversationScope,
    JsonObject,
    JsonValue,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
)

CATEGORY_LABELS = {"timer": "定时提醒"}

MAX_TOTAL_TIMERS = 64
MAX_SCOPE_TIMERS = 8
MIN_DELAY_SECONDS = 5.0
MAX_DELAY_SECONDS = 86_400.0


class ScheduleTimerInput(Model):
    delay_seconds: float = Field(
        ge=MIN_DELAY_SECONDS,
        le=MAX_DELAY_SECONDS,
        description="多少秒后唤醒当前会话（5 秒至 86400 秒即 24 小时内）",
    )
    note: str = Field(
        min_length=1,
        max_length=500,
        description="到期唤醒时提供给自己的提醒事项说明",
    )


class ListTimersInput(Model):
    pass


class CancelTimerInput(Model):
    timer_id: str = Field(min_length=1, max_length=64, description="要取消的定时器 ID")


@dataclass
class TimerEntry:
    id: str
    scope: ConversationScope
    created_at: float
    due_at: float
    delay_seconds: float
    note: str
    task: asyncio.Task[None]

    def public(self, *, include_scope: bool = False) -> JsonObject:
        now = time.time()
        payload: JsonObject = {
            "timer_id": self.id,
            "created_at": round(self.created_at, 3),
            "due_at": round(self.due_at, 3),
            "remaining_seconds": round(max(0.0, self.due_at - now), 1),
            "delay_seconds": self.delay_seconds,
            "note": self.note,
        }
        if include_scope:
            payload["scope_key"] = self.scope.key
        return payload


FireCallback = Callable[[ConversationScope, str, str], Awaitable[None]]


class TimerManager:
    def __init__(self, on_fire: FireCallback) -> None:
        self.on_fire = on_fire
        self.timers: dict[str, TimerEntry] = {}
        self.closed = False

    def schedule(self, scope: ConversationScope, delay_seconds: float, note: str) -> TimerEntry:
        if self.closed:
            raise ValueError("服务正在关闭，无法创建新的定时提醒")
        if len(self.timers) >= MAX_TOTAL_TIMERS:
            raise ValueError(f"全局活动定时器已达上限（{MAX_TOTAL_TIMERS} 个）")
        scope_count = sum(1 for item in self.timers.values() if item.scope == scope)
        if scope_count >= MAX_SCOPE_TIMERS:
            raise ValueError(f"当前会话的活动定时器已达上限（{MAX_SCOPE_TIMERS} 个）")
        timer_id = "tmr_" + uuid4().hex[:10]
        now = time.time()
        due_at = now + delay_seconds

        async def runner() -> None:
            try:
                await asyncio.sleep(delay_seconds)
            except asyncio.CancelledError:
                return
            entry = self.timers.pop(timer_id, None)
            if entry is None or self.closed:
                return
            await self.on_fire(entry.scope, entry.id, entry.note)

        task = asyncio.create_task(runner())
        entry = TimerEntry(
            id=timer_id,
            scope=scope,
            created_at=now,
            due_at=due_at,
            delay_seconds=delay_seconds,
            note=note.strip(),
            task=task,
        )
        self.timers[timer_id] = entry
        return entry

    def list_for_scope(self, scope: ConversationScope) -> list[JsonValue]:
        items = sorted(
            (item for item in self.timers.values() if item.scope == scope),
            key=lambda item: item.due_at,
        )
        return [item.public() for item in items]

    def snapshot(self) -> list[JsonValue]:
        items = sorted(self.timers.values(), key=lambda item: item.due_at)
        return [item.public(include_scope=True) for item in items]

    def cancel(self, timer_id: str, *, scope: ConversationScope | None = None) -> bool:
        entry = self.timers.get(timer_id)
        if entry is None:
            return False
        if scope is not None and entry.scope != scope:
            return False
        self.timers.pop(timer_id, None)
        entry.task.cancel()
        return True

    async def close(self) -> None:
        self.closed = True
        tasks = [entry.task for entry in self.timers.values()]
        self.timers.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def build_tools(manager: TimerManager) -> list[ToolDefinition]:
    async def schedule_tool(context: ToolContext, request: ScheduleTimerInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        entry = manager.schedule(context.scope, request.delay_seconds, request.note)
        return ToolResult.success(
            {
                **entry.public(),
                "notice": "定时器仅保存在运行内存中；若服务重启将自动清空。",
            }
        )

    async def list_tool(context: ToolContext, _request: ListTimersInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        items = manager.list_for_scope(context.scope)
        return ToolResult.success({"timers": items, "total": len(items)})

    async def cancel_tool(context: ToolContext, request: CancelTimerInput) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        removed = manager.cancel(request.timer_id.strip(), scope=context.scope)
        if not removed:
            return ToolResult.failure(
                "未找到属于当前会话的该定时器（可能已触发或已取消）", "TimerNotFound"
            )
        return ToolResult.success({"cancelled": request.timer_id.strip()})

    return [
        ToolDefinition(
            "timer__schedule",
            "为当前会话设定一个延时唤醒提醒（5 秒至 24 小时内，保存在内存中，重启清空）。到期后系统会自动唤醒你继续跟进或提醒群友。",
            "timer",
            model_binding(ScheduleTimerInput, schedule_tool),
            title="创建定时提醒",
        ),
        ToolDefinition(
            "timer__list",
            "查看当前会话尚未触发的内存定时提醒列表。",
            "timer",
            model_binding(ListTimersInput, list_tool),
            concurrency_safe=True,
            title="查看定时提醒",
        ),
        ToolDefinition(
            "timer__cancel",
            "取消当前会话中指定的未到期定时提醒。",
            "timer",
            model_binding(CancelTimerInput, cancel_tool),
            title="取消定时提醒",
        ),
    ]
