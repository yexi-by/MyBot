"""NapCat 群聊历史消息信息工具。"""

from datetime import datetime, timedelta

from app.models import GroupMessage, Image, JsonObject, JsonValue, Text, to_json_value
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

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记群历史消息工具。"""
        registry.register_tool(
            name="qq__get_group_history_messages",
            description=(
                "信息工具：查看当前群的群聊历史消息。"
                "只读取本地 Redis 缓存，不请求 NapCat 远端历史；不允许指定 group_id，始终限定为当前群。"
                "支持三种查询模式：recent_count 查询最近 N 条；recent_duration 查询最近若干分钟；"
                "date_range 按北京时间起止范围查询。"
                "当用户让你回顾刚才群里聊了什么、总结最近对话、查找最近某人说过的话时使用。"
                "此工具只返回历史消息，不发送群消息。"
            ),
            parameters_model=GetGroupHistoryMessagesArgs,
            handler=self.get_group_history_messages,
        )

    async def get_group_history_messages(self, arguments: JsonObject) -> JsonValue:
        """从 Redis 读取当前群聊天记录。"""
        args = GetGroupHistoryMessagesArgs.model_validate(arguments)
        messages = await self._search_history_messages(args=args)
        if messages is None:
            return self._empty_history_result(args=args)
        if not isinstance(messages, list):
            raise TypeError("群历史消息查询返回了非列表结果")
        group_messages = [
            message for message in messages if isinstance(message, GroupMessage)
        ][: args.limit]
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

    async def _search_history_messages(
        self, *, args: GetGroupHistoryMessagesArgs
    ) -> CachedNapCatMessage | list[CachedNapCatMessage] | None:
        """按查询模式读取 Redis 群历史消息。"""
        if args.query_mode == "recent_count":
            return await self.database.search_messages(
                self_id=self.event.self_id,
                group_id=self.event.group_id,
                limit_tuple=(0, args.limit),
            )
        min_time, max_time = self._resolve_history_time_range(args=args)
        return await self.database.search_messages(
            self_id=self.event.self_id,
            group_id=self.event.group_id,
            min_time=min_time,
            max_time=max_time,
        )

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
        return summary

    def _format_history_message(self, message: GroupMessage) -> JsonObject:
        """将 Redis 群消息压缩成适合模型阅读的历史记录。"""
        return {
            "time": message.time,
            "time_text": self._format_history_time(timestamp=message.time),
            "message_id": to_json_value(message.message_id),
            "user_id": to_json_value(message.user_id),
            "member_name": self._resolve_member_name(message=message),
            "role": message.sender.role,
            "text": self._extract_text(message=message),
            "has_image": any(isinstance(segment, Image) for segment in message.message),
        }

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

    def _extract_text(self, message: GroupMessage) -> str:
        """提取群消息中的纯文本。"""
        text_parts = [
            segment.data.text for segment in message.message if isinstance(segment, Text)
        ]
        return "".join(text_parts).strip()
