"""NapCat 群聊合并转发信息工具。"""

import json
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from app.models import (
    Forward,
    GroupMessage,
    JsonObject,
    JsonValue,
    MessageSegment,
    NapCatId,
    Node,
    Response,
    to_json_value,
)
from app.services.llm.tools import LLMToolRegistry
from app.services.napcat.message_formatter import NapCatMessageTextFormatter

from .arguments import GetForwardMessageArgs
from .protocols import NapCatGroupToolBot


@dataclass
class ForwardReadResult:
    """描述一次合并转发树读取结果。"""

    message_id: NapCatId
    complete: bool
    messages: list[JsonObject]
    readable_text: str
    errors: list[JsonObject]
    root_failed: bool = False


class GroupForwardToolset:
    """把合并转发消息读取能力暴露为 LLM 信息工具。"""

    def __init__(self, *, bot: NapCatGroupToolBot, event: GroupMessage) -> None:
        """绑定当前群事件和 NapCat Bot。"""
        self.bot: NapCatGroupToolBot = bot
        self.event: GroupMessage = event
        self.message_formatter: NapCatMessageTextFormatter = NapCatMessageTextFormatter()
        self.segment_adapter: TypeAdapter[MessageSegment] = TypeAdapter(MessageSegment)
        self.segments_adapter: TypeAdapter[list[MessageSegment]] = TypeAdapter(
            list[MessageSegment]
        )

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记合并转发读取工具。"""
        registry.register_tool(
            name="qq__get_forward_message",
            description=(
                "信息工具：读取合并转发消息完整内容。"
                "当你看到合并转发 ID 且需要其中聊天记录辅助回答时，应主动调用；"
                "返回原始消息段 JSON、可读文本和嵌套合并转发内容，不发送群消息。"
            ),
            parameters_model=GetForwardMessageArgs,
            handler=self.get_forward_message,
        )

    async def get_forward_message(self, arguments: JsonObject) -> JsonValue:
        """读取合并转发消息并返回完整结构化内容。"""
        args = GetForwardMessageArgs.model_validate(arguments)
        result = await self._read_forward_tree(
            message_id=args.message_id,
            active_forward_ids=set(),
        )
        if result.root_failed:
            return self._build_root_error_result(args=args, result=result)
        return {
            "ok": True,
            "action": "get_forward_message",
            "group_id": to_json_value(self.event.group_id),
            "message_id": to_json_value(result.message_id),
            "complete": result.complete,
            "messages": to_json_value(result.messages),
            "readable_text": result.readable_text,
            "errors": to_json_value(result.errors),
        }

    def _build_root_error_result(
        self, *, args: GetForwardMessageArgs, result: ForwardReadResult
    ) -> JsonObject:
        """构造根合并转发读取失败时的模型可读结果。"""
        first_error = result.errors[0] if result.errors else {}
        error_type = first_error.get("error_type")
        error = first_error.get("error")
        return {
            "ok": False,
            "is_error": True,
            "action": "get_forward_message",
            "group_id": to_json_value(self.event.group_id),
            "message_id": args.message_id,
            "complete": False,
            "error_type": error_type if isinstance(error_type, str) else "ForwardError",
            "error": error if isinstance(error, str) else "合并转发消息读取失败",
            "message": "合并转发消息读取失败。请根据错误信息修正 message_id 或改用其他回复方式。",
            "messages": [],
            "readable_text": "",
            "errors": to_json_value(result.errors),
        }

    async def _read_forward_tree(
        self,
        *,
        message_id: NapCatId,
        active_forward_ids: set[NapCatId],
    ) -> ForwardReadResult:
        """通过 NapCat 读取指定合并转发 ID 对应的消息树。"""
        if message_id in active_forward_ids:
            error = self._build_cycle_error(message_id=message_id)
            return ForwardReadResult(
                message_id=message_id,
                complete=False,
                messages=[],
                readable_text="",
                errors=[error],
            )
        active_forward_ids.add(message_id)
        try:
            response = await self._safe_get_forward_msg(message_id=message_id)
            if response.status != "ok" or response.retcode != 0:
                error = self._build_response_error(
                    message_id=message_id,
                    response=response,
                )
                return ForwardReadResult(
                    message_id=message_id,
                    complete=False,
                    messages=[],
                    readable_text="",
                    errors=[error],
                    root_failed=True,
                )
            raw_messages = self._extract_response_messages(
                message_id=message_id,
                response=response,
            )
            if raw_messages is None:
                error = self._build_invalid_response_error(
                    message_id=message_id,
                    response=response,
                )
                return ForwardReadResult(
                    message_id=message_id,
                    complete=False,
                    messages=[],
                    readable_text="",
                    errors=[error],
                    root_failed=True,
                )
            messages, errors = await self._build_forward_messages(
                raw_messages=raw_messages,
                active_forward_ids=active_forward_ids,
            )
            readable_text = self._build_readable_text(messages=messages)
            return ForwardReadResult(
                message_id=message_id,
                complete=not errors,
                messages=messages,
                readable_text=readable_text,
                errors=errors,
            )
        except Exception as exc:
            error = {
                "message_id": to_json_value(message_id),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            return ForwardReadResult(
                message_id=message_id,
                complete=False,
                messages=[],
                readable_text="",
                errors=[error],
                root_failed=True,
            )
        finally:
            active_forward_ids.discard(message_id)

    async def _read_embedded_forward_tree(
        self,
        *,
        message_id: NapCatId,
        content: JsonValue,
        active_forward_ids: set[NapCatId],
    ) -> ForwardReadResult:
        """解析已经内嵌在消息段中的合并转发内容。"""
        if message_id in active_forward_ids:
            error = self._build_cycle_error(message_id=message_id)
            return ForwardReadResult(
                message_id=message_id,
                complete=False,
                messages=[],
                readable_text="",
                errors=[error],
            )
        active_forward_ids.add(message_id)
        try:
            raw_messages = self._extract_embedded_messages(content=content)
            messages, errors = await self._build_forward_messages(
                raw_messages=raw_messages,
                active_forward_ids=active_forward_ids,
            )
            return ForwardReadResult(
                message_id=message_id,
                complete=not errors,
                messages=messages,
                readable_text=self._build_readable_text(messages=messages),
                errors=errors,
            )
        finally:
            active_forward_ids.discard(message_id)

    async def _safe_get_forward_msg(self, *, message_id: NapCatId) -> Response:
        """调用 NapCat 获取合并转发详情。"""
        return await self.bot.get_forward_msg(message_id=message_id)

    def _extract_response_messages(
        self, *, message_id: NapCatId, response: Response
    ) -> list[JsonValue] | None:
        """从 NapCat get_forward_msg 响应中提取消息列表。"""
        _ = message_id
        data = response.data
        if not isinstance(data, dict):
            return None
        messages = data.get("messages")
        if not isinstance(messages, list):
            return None
        return [to_json_value(message) for message in messages]

    def _extract_embedded_messages(self, *, content: JsonValue) -> list[JsonValue]:
        """把内嵌合并转发内容标准化为消息条目列表。"""
        if isinstance(content, dict):
            messages = self._extract_messages_field(payload=content)
            if messages is not None:
                return messages
            nested_content = self._extract_forward_item_content(payload=content)
            if nested_content is not None:
                return self._extract_embedded_messages(content=nested_content)
            return [content]
        if isinstance(content, list):
            if self._is_message_segments_payload(value=content):
                return [{"message": content}]
            return [to_json_value(item) for item in content]
        return [{"message": content}]

    async def _build_forward_messages(
        self,
        *,
        raw_messages: list[JsonValue],
        active_forward_ids: set[NapCatId],
    ) -> tuple[list[JsonObject], list[JsonObject]]:
        """将原始合并转发条目转换为完整结构化结果。"""
        messages: list[JsonObject] = []
        errors: list[JsonObject] = []
        for index, raw_message in enumerate(raw_messages, start=1):
            message, message_errors = await self._build_forward_message(
                index=index,
                raw_message=raw_message,
                active_forward_ids=active_forward_ids,
            )
            messages.append(message)
            errors.extend(message_errors)
        return messages, errors

    async def _build_forward_message(
        self,
        *,
        index: int,
        raw_message: JsonValue,
        active_forward_ids: set[NapCatId],
    ) -> tuple[JsonObject, list[JsonObject]]:
        """转换单条合并转发消息。"""
        payload = raw_message if isinstance(raw_message, dict) else None
        content = self._extract_message_content(raw_message=raw_message)
        segments = self._parse_segments_from_content(content=content)
        nested_forwards: list[JsonObject] = []
        errors: list[JsonObject] = []
        if segments is not None:
            nested_forwards, errors = await self._read_nested_forwards(
                segments=segments,
                active_forward_ids=active_forward_ids,
            )
        message: JsonObject = {
            "index": index,
            "raw": to_json_value(raw_message),
            "sender": self._extract_sender(payload=payload),
            "time": self._extract_optional_field(payload=payload, keys=("time",)),
            "message_id": self._extract_optional_field(
                payload=payload,
                keys=("message_id", "msg_id", "id"),
            ),
            "content": to_json_value(content),
            "segments": self._dump_segments(content=content, segments=segments),
            "segment_types": [segment.type for segment in segments or []],
            "text": self._format_content_text(content=content, segments=segments),
            "nested_forwards": to_json_value(nested_forwards),
        }
        return message, errors

    async def _read_nested_forwards(
        self,
        *,
        segments: list[MessageSegment],
        active_forward_ids: set[NapCatId],
    ) -> tuple[list[JsonObject], list[JsonObject]]:
        """读取消息段中出现的嵌套合并转发。"""
        nested_results: list[JsonObject] = []
        errors: list[JsonObject] = []
        for segment in self._collect_forward_segments(segments=segments):
            if segment.data.content is None:
                result = await self._read_forward_tree(
                    message_id=segment.data.id,
                    active_forward_ids=active_forward_ids,
                )
            else:
                result = await self._read_embedded_forward_tree(
                    message_id=segment.data.id,
                    content=segment.data.content,
                    active_forward_ids=active_forward_ids,
                )
            nested_results.append(self._result_to_json(result=result))
            errors.extend(result.errors)
        return nested_results, errors

    def _collect_forward_segments(self, *, segments: list[MessageSegment]) -> list[Forward]:
        """收集消息段及节点内容中的合并转发段。"""
        forwards: list[Forward] = []
        for segment in segments:
            if isinstance(segment, Forward):
                forwards.append(segment)
                continue
            if isinstance(segment, Node) and isinstance(segment.data.content, list):
                forwards.extend(
                    self._collect_forward_segments(segments=segment.data.content)
                )
        return forwards

    def _extract_message_content(self, *, raw_message: JsonValue) -> JsonValue:
        """从原始条目中提取消息正文或消息段列表。"""
        if isinstance(raw_message, dict):
            content = self._extract_forward_item_content(payload=raw_message)
            if content is not None:
                return content
        return raw_message

    def _extract_forward_item_content(self, *, payload: JsonObject) -> JsonValue:
        """从合并转发条目中提取正文或消息段内容。"""
        for key in ("message", "content", "messages"):
            if key in payload and payload[key] is not None:
                return payload[key]
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("message", "content", "messages"):
                if key in data and data[key] is not None:
                    return data[key]
        return None

    def _extract_messages_field(self, *, payload: JsonObject) -> list[JsonValue] | None:
        """从对象中提取 messages 字段。"""
        for key in ("messages", "message", "content"):
            value = payload.get(key)
            if isinstance(value, list) and not self._is_message_segments_payload(
                value=value
            ):
                return [to_json_value(item) for item in value]
        data = payload.get("data")
        if isinstance(data, dict):
            data_payload: JsonObject = data
            return self._extract_messages_field(payload=data_payload)
        return None

    def _parse_segments_from_content(
        self, *, content: JsonValue
    ) -> list[MessageSegment] | None:
        """尝试把消息内容解析为消息段列表。"""
        if isinstance(content, list):
            try:
                return self.segments_adapter.validate_python(content)
            except ValidationError:
                return None
        if isinstance(content, dict) and "type" in content:
            try:
                return [self.segment_adapter.validate_python(content)]
            except ValidationError:
                return None
        return None

    def _dump_segments(
        self, *, content: JsonValue, segments: list[MessageSegment] | None
    ) -> JsonValue:
        """输出完整原始消息段。"""
        if segments is None:
            return []
        return to_json_value(content)

    def _format_content_text(
        self, *, content: JsonValue, segments: list[MessageSegment] | None
    ) -> str:
        """生成辅助阅读文本。"""
        if segments is not None:
            return self.message_formatter.format_segments(
                segments=segments,
                images_attached=False,
            )
        if content is None:
            return "（无文本内容）"
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))

    def _extract_sender(self, *, payload: JsonObject | None) -> JsonValue:
        """提取发送者信息。"""
        if payload is None:
            return None
        sender = payload.get("sender")
        if sender is not None:
            return to_json_value(sender)
        data = payload.get("data")
        if isinstance(data, dict):
            return to_json_value(data.get("sender"))
        return None

    def _extract_optional_field(
        self, *, payload: JsonObject | None, keys: tuple[str, ...]
    ) -> JsonValue:
        """按候选字段提取元信息。"""
        if payload is None:
            return None
        for key in keys:
            if key in payload:
                return payload[key]
        data = payload.get("data")
        if isinstance(data, dict):
            for key in keys:
                if key in data:
                    return data[key]
        return None

    def _is_message_segments_payload(self, *, value: list[JsonValue]) -> bool:
        """判断列表是否是消息段数组。"""
        if not value:
            return True
        for item in value:
            if not isinstance(item, dict):
                return False
            raw_type = item.get("type")
            if not isinstance(raw_type, str):
                return False
        return True

    def _build_readable_text(self, *, messages: list[JsonObject]) -> str:
        """把完整结构化消息组装成辅助阅读文本。"""
        lines: list[str] = []
        for message in messages:
            index = message.get("index")
            sender = self._format_sender_label(sender=message.get("sender"))
            text = message.get("text")
            line = f"{index}. "
            if sender is not None:
                line += f"{sender}: "
            line += text if isinstance(text, str) else "（无文本内容）"
            lines.append(line)
            nested_forwards = message.get("nested_forwards")
            if isinstance(nested_forwards, list):
                for nested_forward in nested_forwards:
                    if isinstance(nested_forward, dict):
                        lines.extend(
                            self._format_nested_forward_lines(
                                nested_forward=nested_forward
                            )
                        )
        return "\n".join(lines)

    def _format_nested_forward_lines(
        self, *, nested_forward: JsonObject
    ) -> list[str]:
        """生成嵌套合并转发的辅助阅读文本行。"""
        message_id = nested_forward.get("message_id")
        readable_text = nested_forward.get("readable_text")
        if not isinstance(message_id, str):
            message_id = "未知"
        lines = [f"  （嵌套合并转发，ID: {message_id}）"]
        if isinstance(readable_text, str) and readable_text:
            for line in readable_text.splitlines():
                lines.append(f"  {line}")
        return lines

    def _format_sender_label(self, *, sender: JsonValue) -> str | None:
        """把发送者对象格式化为短标签。"""
        if not isinstance(sender, dict):
            return None
        nickname = self._find_sender_text(sender=sender, keys=("card", "nickname", "name"))
        user_id = self._find_sender_text(sender=sender, keys=("user_id", "uin", "qq"))
        if nickname is not None and user_id is not None:
            return f"{nickname} ({user_id})"
        if nickname is not None:
            return nickname
        return user_id

    def _find_sender_text(
        self, *, sender: JsonObject, keys: tuple[str, ...]
    ) -> str | None:
        """按候选键查找发送者文本。"""
        for key in keys:
            value = sender.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text:
                    return text
        return None

    def _result_to_json(self, *, result: ForwardReadResult) -> JsonObject:
        """把内部读取结果转换为 JSON 对象。"""
        return {
            "message_id": to_json_value(result.message_id),
            "complete": result.complete,
            "messages": to_json_value(result.messages),
            "readable_text": result.readable_text,
            "errors": to_json_value(result.errors),
        }

    def _build_cycle_error(self, *, message_id: NapCatId) -> JsonObject:
        """构造循环嵌套错误。"""
        return {
            "message_id": to_json_value(message_id),
            "error_type": "ForwardCycle",
            "error": f"检测到合并转发循环嵌套: {message_id}",
        }

    def _build_response_error(
        self, *, message_id: NapCatId, response: Response
    ) -> JsonObject:
        """构造 NapCat 失败响应错误。"""
        return {
            "message_id": to_json_value(message_id),
            "error_type": "NapCatActionFailed",
            "error": response.message or response.wording or "NapCat 返回失败",
            "response": to_json_value(response),
        }

    def _build_invalid_response_error(
        self, *, message_id: NapCatId, response: Response
    ) -> JsonObject:
        """构造响应结构不符合预期的错误。"""
        return {
            "message_id": to_json_value(message_id),
            "error_type": "InvalidForwardResponse",
            "error": "NapCat get_forward_msg 响应缺少 data.messages 数组",
            "response": to_json_value(response),
        }
