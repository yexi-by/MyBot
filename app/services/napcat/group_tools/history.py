"""NapCat 群聊历史消息信息工具。"""

from datetime import datetime, timedelta

from app.database import GroupDataScope, GroupMessageReader, StoredGroupMessage
from app.models import GroupMessage, Image, JsonObject, JsonValue, to_json_value
from app.services.napcat.message_formatter import NapCatMessageTextFormatter
from app.services.llm.tools import LLMToolRegistry

from .arguments import (
    BEIJING_TIMEZONE,
    GetGroupHistoryMessagesArgs,
    HISTORY_TIME_FORMAT,
)


class GroupHistoryToolset:
    """通过群消息仓库向 LLM 暴露当前群历史消息。"""

    def __init__(
        self, *, group_messages: GroupMessageReader, event: GroupMessage
    ) -> None:
        """绑定当前群事件与消息数据库。"""
        self.group_messages: GroupMessageReader = group_messages
        self.event: GroupMessage = event
        self.message_formatter: NapCatMessageTextFormatter = NapCatMessageTextFormatter()

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记群历史消息工具。"""
        registry.register_tool(
            name="qq__get_group_history_messages",
            description=(
                "信息工具：读取当前群未撤回的历史消息，只限当前群，不发送消息。"
                "需要近期上下文、确认某人说过什么、回看某条消息前后对话时主动调用。"
                "query_mode 支持 recent_count、recent_duration、date_range、around_message；"
                "按 QQ 过滤填 user_id，查消息上下文填 context_message_id。"
            ),
            parameters_model=GetGroupHistoryMessagesArgs,
            handler=self.get_group_history_messages,
        )

    async def get_group_history_messages(self, arguments: JsonObject) -> JsonValue:
        """读取当前群未撤回的聊天记录。"""
        args = GetGroupHistoryMessagesArgs.model_validate(arguments)
        if args.query_mode == "around_message":
            return await self._get_around_history_messages(args=args)
        group_messages = await self._search_history_messages(args=args)
        return {
            "ok": True,
            "action": "get_group_history_messages",
            "query": self._build_history_query_summary(args=args),
            "group_id": to_json_value(self.event.group_id),
            "messages": [
                self._format_history_message(message=message)
                for message in group_messages
            ],
        }

    async def _get_around_history_messages(
        self, *, args: GetGroupHistoryMessagesArgs
    ) -> JsonObject:
        """按数据库中的任意未撤回锚点读取前后文。"""
        if args.context_message_id is None:
            raise ValueError("around_message 模式必须填写 context_message_id")
        context_messages = await self.group_messages.list_around(
            scope=self._scope(),
            message_id=args.context_message_id,
            before_count=args.before_count,
            after_count=args.after_count,
            sender_id=args.user_id,
        )
        return {
            "ok": True,
            "action": "get_group_history_messages",
            "query": self._build_history_query_summary(args=args),
            "group_id": to_json_value(self.event.group_id),
            "messages": [
                self._format_history_message(
                    message=message,
                    is_anchor=message.message_id == args.context_message_id,
                )
                for message in context_messages
            ],
        }

    async def _search_history_messages(
        self, *, args: GetGroupHistoryMessagesArgs
    ) -> list[StoredGroupMessage]:
        """按查询模式读取群历史消息。"""
        if args.query_mode == "recent_count":
            return await self.group_messages.list_recent(
                scope=self._scope(),
                limit=args.limit,
                sender_id=args.user_id,
            )
        start, end = self._resolve_history_time_range(args=args)
        return await self.group_messages.list_between(
            scope=self._scope(),
            start=start,
            end=end,
            limit=args.limit,
            sender_id=args.user_id,
        )

    def _resolve_history_time_range(
        self, *, args: GetGroupHistoryMessagesArgs
    ) -> tuple[datetime, datetime]:
        """将历史查询参数转换为带时区的半开时间范围。"""
        if args.query_mode == "recent_duration":
            if args.duration_minutes is None:
                raise ValueError("recent_duration 模式必须填写 duration_minutes")
            now = datetime.now(BEIJING_TIMEZONE)
            start = now - timedelta(minutes=args.duration_minutes)
            return start, now
        if args.start_time is None or args.end_time is None:
            raise ValueError("date_range 模式必须填写 start_time 和 end_time")
        start = self._parse_history_time(value=args.start_time, field_name="start_time")
        end = self._parse_history_time(value=args.end_time, field_name="end_time")
        if start >= end:
            raise ValueError("start_time 必须早于 end_time")
        return start, end

    def _scope(self) -> GroupDataScope:
        """返回与当前群事件绑定的数据作用域。"""
        return GroupDataScope(
            bot_id=self.event.self_id,
            group_id=self.event.group_id,
        )

    def _parse_history_time(self, *, value: str, field_name: str) -> datetime:
        """解析北京时间历史查询时间。"""
        try:
            naive_time = datetime.strptime(value, HISTORY_TIME_FORMAT)
        except ValueError as exc:
            raise ValueError(
                f"{field_name} 格式必须是 YYYY-MM-DD HH:MM:SS，北京时间"
            ) from exc
        return naive_time.replace(tzinfo=BEIJING_TIMEZONE)

    def _build_history_query_summary(
        self, *, args: GetGroupHistoryMessagesArgs
    ) -> JsonObject:
        """生成历史查询参数摘要。"""
        summary: JsonObject = {
            "query_mode": args.query_mode,
            "limit": args.limit,
        }
        if args.duration_minutes is not None:
            summary["duration_minutes"] = args.duration_minutes
        if args.start_time is not None:
            summary["start_time"] = args.start_time
        if args.end_time is not None:
            summary["end_time"] = args.end_time
        if args.user_id is not None:
            summary["user_id"] = args.user_id
        if args.query_mode == "around_message":
            if args.context_message_id is not None:
                summary["context_message_id"] = args.context_message_id
            summary["before_count"] = args.before_count
            summary["after_count"] = args.after_count
        return summary

    def _format_history_message(
        self, message: StoredGroupMessage, *, is_anchor: bool = False
    ) -> JsonObject:
        """将群消息压缩成适合模型阅读的历史记录。"""
        timestamp = int(message.occurred_at.timestamp())
        result: JsonObject = {
            "time": timestamp,
            "time_text": self._format_history_time(timestamp=timestamp),
            "message_id": to_json_value(message.message_id),
            "user_id": to_json_value(message.sender_id),
            "member_name": message.sender_name or "未知群员",
            "role": message.sender_role,
            "text": self.message_formatter.format_segments(
                segments=list(message.segments),
                images_attached=False,
            ),
            "segment_types": [segment.type for segment in message.segments],
            "has_image": any(isinstance(segment, Image) for segment in message.segments),
        }
        if is_anchor:
            result["is_anchor"] = True
        return result

    def _format_history_time(self, *, timestamp: int) -> str:
        """将 Unix 秒级时间戳格式化为北京时间。"""
        return datetime.fromtimestamp(timestamp, tz=BEIJING_TIMEZONE).strftime(
            HISTORY_TIME_FORMAT
        )
