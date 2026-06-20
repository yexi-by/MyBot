"""NapCat 群聊历史消息信息工具。"""

from datetime import datetime, timedelta

from app.models import GroupMessage, Image, JsonObject, JsonValue, to_json_value
from app.services.napcat.message_formatter import NapCatMessageTextFormatter
from app.services.llm.tools import LLMToolRegistry

from .arguments import (
    BEIJING_TIMEZONE,
    GetGroupHistoryMessagesArgs,
    HISTORY_TIME_FORMAT,
)
from .protocols import CachedNapCatMessage, NapCatGroupHistoryDatabase


class GroupHistoryToolset:
    """通过 Redis 缓存向 LLM 暴露当前群历史消息。"""

    def __init__(
        self, *, database: NapCatGroupHistoryDatabase, event: GroupMessage
    ) -> None:
        """绑定当前群事件与消息数据库。"""
        self.database: NapCatGroupHistoryDatabase = database
        self.event: GroupMessage = event
        self.message_formatter: NapCatMessageTextFormatter = NapCatMessageTextFormatter()

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记群历史消息工具。"""
        registry.register_tool(
            name="qq__get_group_history_messages",
            description=(
                "信息工具：读当前群 Redis 历史，只限当前群，不发送消息。"
                "需要近期上下文、确认某人说过什么、回看某条消息前后对话时主动调用。"
                "query_mode 支持 recent_count、recent_duration、date_range、around_message；"
                "按 QQ 过滤填 user_id，查消息上下文填 context_message_id。"
            ),
            parameters_model=GetGroupHistoryMessagesArgs,
            handler=self.get_group_history_messages,
        )

    async def get_group_history_messages(self, arguments: JsonObject) -> JsonValue:
        """从 Redis 读取当前群聊天记录。"""
        args = GetGroupHistoryMessagesArgs.model_validate(arguments)
        if args.query_mode == "around_message":
            return await self._get_around_history_messages(args=args)
        messages = await self._search_history_messages(args=args)
        if messages is None:
            return self._empty_history_result(args=args)
        if not isinstance(messages, list):
            raise TypeError("群历史消息查询返回了非列表结果")
        group_messages = [
            message for message in messages if isinstance(message, GroupMessage)
        ]
        group_messages = self._filter_group_messages_by_user(
            messages=group_messages,
            user_id=args.user_id,
        )[: args.limit]
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
        """在最近扫描窗口内查找锚点消息并返回前后文。"""
        messages = await self._search_history_messages(args=args)
        if messages is None:
            return self._empty_history_result(args=args)
        if not isinstance(messages, list):
            raise TypeError("群历史消息查询返回了非列表结果")
        group_messages = [
            message for message in messages if isinstance(message, GroupMessage)
        ]
        context_messages = self._select_context_messages(
            messages=group_messages,
            args=args,
        )
        context_messages = self._filter_group_messages_by_user(
            messages=context_messages,
            user_id=args.user_id,
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
    ) -> CachedNapCatMessage | list[CachedNapCatMessage] | None:
        """按查询模式读取 Redis 群历史消息。"""
        if args.query_mode == "recent_count":
            scan_limit = args.scan_limit if args.user_id is not None else args.limit
            return await self.database.search_messages(
                self_id=self.event.self_id,
                group_id=self.event.group_id,
                limit_tuple=(0, scan_limit),
            )
        if args.query_mode == "around_message":
            return await self.database.search_messages(
                self_id=self.event.self_id,
                group_id=self.event.group_id,
                limit_tuple=(0, args.scan_limit),
            )
        min_time, max_time = self._resolve_history_time_range(args=args)
        return await self.database.search_messages(
            self_id=self.event.self_id,
            group_id=self.event.group_id,
            min_time=min_time,
            max_time=max_time,
        )

    def _filter_group_messages_by_user(
        self, *, messages: list[GroupMessage], user_id: str | None
    ) -> list[GroupMessage]:
        """按 QQ 号筛选群消息；未指定时保持原列表。"""
        if user_id is None:
            return messages
        return [message for message in messages if message.user_id == user_id]

    def _select_context_messages(
        self, *, messages: list[GroupMessage], args: GetGroupHistoryMessagesArgs
    ) -> list[GroupMessage]:
        """从扫描结果中按时间正序截取锚点消息前后文。"""
        if args.context_message_id is None:
            raise ValueError("around_message 模式必须填写 context_message_id")
        chronological_messages = sorted(
            messages,
            key=lambda message: (message.time, str(message.message_id)),
        )
        anchor_index: int | None = None
        for index, message in enumerate(chronological_messages):
            if message.message_id == args.context_message_id:
                anchor_index = index
                break
        if anchor_index is None:
            return []
        start_index = max(0, anchor_index - args.before_count)
        end_index = min(
            len(chronological_messages),
            anchor_index + args.after_count + 1,
        )
        return chronological_messages[start_index:end_index]

    def _resolve_history_time_range(
        self, *, args: GetGroupHistoryMessagesArgs
    ) -> tuple[int, int]:
        """将历史查询参数转换为 Unix 秒级时间范围。"""
        if args.query_mode == "recent_duration":
            if args.duration_minutes is None:
                raise ValueError("recent_duration 模式必须填写 duration_minutes")
            now = datetime.now(BEIJING_TIMEZONE)
            start = now - timedelta(minutes=args.duration_minutes)
            return int(start.timestamp()), int(now.timestamp())
        if args.start_time is None or args.end_time is None:
            raise ValueError("date_range 模式必须填写 start_time 和 end_time")
        start = self._parse_history_time(value=args.start_time, field_name="start_time")
        end = self._parse_history_time(value=args.end_time, field_name="end_time")
        if start > end:
            raise ValueError("start_time 不能晚于 end_time")
        return int(start.timestamp()), int(end.timestamp())

    def _parse_history_time(self, *, value: str, field_name: str) -> datetime:
        """解析北京时间历史查询时间。"""
        try:
            naive_time = datetime.strptime(value, HISTORY_TIME_FORMAT)
        except ValueError as exc:
            raise ValueError(
                f"{field_name} 格式必须是 YYYY-MM-DD HH:MM:SS，北京时间"
            ) from exc
        return naive_time.replace(tzinfo=BEIJING_TIMEZONE)

    def _empty_history_result(self, *, args: GetGroupHistoryMessagesArgs) -> JsonObject:
        """生成空历史消息查询结果。"""
        return {
            "ok": True,
            "action": "get_group_history_messages",
            "query": self._build_history_query_summary(args=args),
            "group_id": to_json_value(self.event.group_id),
            "messages": [],
        }

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
            summary["scan_limit"] = args.scan_limit
        elif args.user_id is not None:
            summary["scan_limit"] = args.scan_limit
        return summary

    def _format_history_message(
        self, message: GroupMessage, *, is_anchor: bool = False
    ) -> JsonObject:
        """将 Redis 群消息压缩成适合模型阅读的历史记录。"""
        result: JsonObject = {
            "time": message.time,
            "time_text": self._format_history_time(timestamp=message.time),
            "message_id": to_json_value(message.message_id),
            "user_id": to_json_value(message.user_id),
            "member_name": self._resolve_member_name(message=message),
            "role": message.sender.role,
            "text": self.message_formatter.format_segments(
                segments=message.message,
                images_attached=False,
            ),
            "segment_types": [segment.type for segment in message.message],
            "has_image": any(isinstance(segment, Image) for segment in message.message),
        }
        if is_anchor:
            result["is_anchor"] = True
        return result

    def _format_history_time(self, *, timestamp: int) -> str:
        """将 Unix 秒级时间戳格式化为北京时间。"""
        return datetime.fromtimestamp(timestamp, tz=BEIJING_TIMEZONE).strftime(
            HISTORY_TIME_FORMAT
        )

    def _resolve_member_name(self, message: GroupMessage) -> str:
        """优先使用群名片，其次使用昵称。"""
        if message.sender.card:
            return message.sender.card
        if message.sender.nickname:
            return message.sender.nickname
        return "未知群员"
