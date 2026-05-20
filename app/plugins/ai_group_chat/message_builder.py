"""AI 群聊插件的 LLM 输入构造器。"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiofiles
import httpx
from pydantic import TypeAdapter, ValidationError

from app.database import RedisDatabaseManager
from app.models import (
    At,
    File,
    Forward,
    GroupMessage,
    Image,
    JsonObject,
    JsonValue,
    LightApp,
    MFace,
    Markdown,
    MessageSegment,
    MiniApp,
    NapCatId,
    Node,
    Reply,
    Share,
    Text,
    UnknownSegment,
)
from app.models import Json as JsonSegment
from app.models import to_json_value
from app.services import ChatMessage
from app.utils.log import log_event

from .config import AIGroupChatConfig
from .constants import (
    BEIJING_TIMEZONE,
    ROLE_LABELS,
)

FIELD_TEXT_LIMIT = 240
JSON_TEXT_LIMIT = 600
MARKDOWN_TEXT_LIMIT = 800
FORWARD_MAX_ITEMS = 8
FORWARD_MAX_DEPTH = 2


@dataclass(frozen=True)
class BuiltTurnMessages:
    """描述本轮构造出的 LLM 输入和图片状态。"""

    turn_messages: list[ChatMessage]
    contains_image: bool
    loaded_image_count: int


class GroupChatMessageBuilder:
    """把 NapCat 群消息转换为适合 LLM 阅读的输入消息。"""

    def __init__(
        self,
        *,
        config: AIGroupChatConfig,
        database: RedisDatabaseManager,
        http_client: httpx.AsyncClient,
    ) -> None:
        """保存构造 LLM 输入所需的服务。"""
        self.config: AIGroupChatConfig = config
        self.database: RedisDatabaseManager = database
        self.http_client: httpx.AsyncClient = http_client
        self.segment_adapter: TypeAdapter[MessageSegment] = TypeAdapter(MessageSegment)
        self.segments_adapter: TypeAdapter[list[MessageSegment]] = TypeAdapter(
            list[MessageSegment]
        )

    async def build_turn_messages(
        self,
        *,
        msg: GroupMessage,
        collect_images: bool,
    ) -> BuiltTurnMessages:
        """构造本轮提交给 LLM 的用户消息。"""
        user_message, contains_image, loaded_image_count = (
            await self._build_user_message_with_metadata(
                msg=msg,
                collect_images=collect_images,
            )
        )
        return BuiltTurnMessages(
            turn_messages=[user_message],
            contains_image=contains_image,
            loaded_image_count=loaded_image_count,
        )

    async def build_user_message(
        self,
        *,
        msg: GroupMessage,
        collect_images: bool | None = None,
    ) -> ChatMessage:
        """把当前群消息和可选引用消息整理为单条 LLM 输入。"""
        if collect_images is None:
            collect_images = self.config.supports_multimodal
        user_message, _, _ = await self._build_user_message_with_metadata(
            msg=msg,
            collect_images=collect_images,
        )
        return user_message

    async def _build_user_message_with_metadata(
        self,
        *,
        msg: GroupMessage,
        collect_images: bool,
    ) -> tuple[ChatMessage, bool, int]:
        """构造用户消息，并返回本轮图片识别与加载结果。"""
        image_bytes: list[bytes] = []
        current_has_image = self._has_image(msg=msg)
        reply_message = await self._load_reply_context_message(msg=msg)
        reply_has_image = reply_message is not None and self._has_image(
            msg=reply_message
        )
        contains_image = current_has_image or reply_has_image
        current_image_bytes: list[bytes] = []
        reply_image_bytes: list[bytes] = []
        log_event(
            level="DEBUG",
            event="ai_group_chat.message_builder.start",
            category="plugin",
            message="开始构造 AI 群聊用户输入",
            group_id=msg.group_id,
            message_id=msg.message_id,
            user_id=msg.user_id,
            collect_images=collect_images,
            contains_image=contains_image,
            segment_count=len(msg.message),
            raw_message=msg.raw_message,
        )
        if collect_images:
            current_image_bytes = await self._collect_message_images(msg=msg)
            image_bytes.extend(current_image_bytes)
            if reply_message is not None:
                reply_image_bytes = await self._collect_message_images(
                    msg=reply_message
                )
                image_bytes.extend(reply_image_bytes)
        text_blocks = [
            self._format_current_message_markdown(
                msg=msg,
                images_attached=len(current_image_bytes) > 0,
            )
        ]
        if reply_message is not None:
            text_blocks.append(
                self._format_quoted_message_markdown(
                    msg=reply_message,
                    images_attached=len(reply_image_bytes) > 0,
                )
            )
        log_event(
            level="DEBUG",
            event="ai_group_chat.message_builder.finished",
            category="plugin",
            message="AI 群聊用户输入构造完成",
            group_id=msg.group_id,
            message_id=msg.message_id,
            text_blocks_count=len(text_blocks),
            text_chars=sum(len(text_block) for text_block in text_blocks),
            image_count=len(image_bytes),
            contains_image=contains_image,
            has_reply_context=reply_message is not None,
        )
        return (
            ChatMessage(
                role="user",
                text="\n\n".join(text_blocks),
                image=image_bytes if image_bytes else None,
            ),
            contains_image,
            len(image_bytes),
        )

    async def _load_reply_context_message(
        self, *, msg: GroupMessage
    ) -> GroupMessage | None:
        """读取当前消息引用的历史群消息。"""
        reply_id = self._extract_reply_id(msg=msg)
        if reply_id is None:
            log_event(
                level="DEBUG",
                event="ai_group_chat.reply_context.none",
                category="plugin",
                message="当前群消息没有引用消息",
                group_id=msg.group_id,
                message_id=msg.message_id,
            )
            return None
        log_event(
            level="DEBUG",
            event="ai_group_chat.reply_context.lookup",
            category="plugin",
            message="开始从 Redis 读取引用消息上下文",
            group_id=msg.group_id,
            message_id=msg.message_id,
            reply_message_id=reply_id,
        )
        stored_message = await self.database.search_messages(
            self_id=msg.self_id,
            group_id=msg.group_id,
            message_id=reply_id,
        )
        if not isinstance(stored_message, GroupMessage):
            log_event(
                level="DEBUG",
                event="ai_group_chat.reply_context.missing",
                category="plugin",
                message="Redis 中没有找到引用消息上下文",
                group_id=msg.group_id,
                message_id=msg.message_id,
                reply_message_id=reply_id,
            )
            return None
        log_event(
            level="DEBUG",
            event="ai_group_chat.reply_context.loaded",
            category="plugin",
            message="已从 Redis 读取引用消息上下文",
            group_id=msg.group_id,
            message_id=msg.message_id,
            reply_message_id=reply_id,
            reply_user_id=stored_message.user_id,
            reply_raw_message=stored_message.raw_message,
            reply_segment_count=len(stored_message.message),
        )
        return stored_message

    async def _collect_message_images(self, *, msg: GroupMessage) -> list[bytes]:
        """收集消息中的图片字节。"""
        image_bytes: list[bytes] = []
        image_segments_count = sum(
            1 for segment in msg.message if isinstance(segment, Image)
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.image_collect.start",
            category="plugin",
            message="开始收集群消息图片",
            group_id=msg.group_id,
            message_id=msg.message_id,
            image_segments_count=image_segments_count,
        )
        for segment in msg.message:
            if not isinstance(segment, Image):
                continue
            try:
                data = await self._load_image_bytes(segment=segment)
            except Exception as exc:
                log_event(
                    level="WARNING",
                    event="ai_group_chat.image_load_failed",
                    category="plugin",
                    message="读取群消息图片失败",
                    message_id=msg.message_id,
                    file=segment.data.file,
                    url=segment.data.url,
                    error=str(exc),
                )
                continue
            if data is not None:
                image_bytes.append(data)
                log_event(
                    level="DEBUG",
                    event="ai_group_chat.image_collect.loaded",
                    category="plugin",
                    message="群消息图片读取成功",
                    group_id=msg.group_id,
                    message_id=msg.message_id,
                    file=segment.data.file,
                    path=segment.data.path,
                    url=segment.data.url,
                    bytes_count=len(data),
                )
                continue
            log_event(
                level="WARNING",
                event="ai_group_chat.image_source_missing",
                category="plugin",
                message="群消息图片没有可读取来源",
                message_id=msg.message_id,
                file=segment.data.file,
                path=segment.data.path,
                url=segment.data.url,
            )
        log_event(
            level="DEBUG",
            event="ai_group_chat.image_collect.finished",
            category="plugin",
            message="群消息图片收集完成",
            group_id=msg.group_id,
            message_id=msg.message_id,
            loaded_image_count=len(image_bytes),
        )
        return image_bytes

    async def _load_image_bytes(self, *, segment: Image) -> bytes | None:
        """优先读取本地图片缓存，失败时回退到图片 URL。"""
        if segment.data.path:
            path = Path(segment.data.path)
            if path.is_file():
                async with aiofiles.open(path, mode="rb") as file:
                    return await file.read()
        if segment.data.url:
            async with self.http_client.stream("GET", segment.data.url) as response:
                _ = response.raise_for_status()
                return await response.aread()
        return None

    def _format_current_message_markdown(
        self, *, msg: GroupMessage, images_attached: bool
    ) -> str:
        """把当前群消息格式化为低噪音 Markdown。"""
        return "\n".join(
            [
                "## 当前消息",
                "",
                f"- 时间: {self._current_time()}",
                f"- 群: {self._format_group_label(msg=msg)}",
                f"- 群员: {self._format_member_label(msg=msg)}",
                "",
                "### 消息",
                "",
                self._format_message_text(
                    msg=msg,
                    images_attached=images_attached,
                ),
            ]
        )

    def _format_quoted_message_markdown(
        self, *, msg: GroupMessage, images_attached: bool
    ) -> str:
        """把被引用的历史消息格式化为 Markdown。"""
        return "\n".join(
            [
                "## 引用消息",
                "",
                "<small>注意：下面内容是本次发言引用的历史消息，只用于理解上下文，不是用户这次真正说的正文。</small>",
                "",
                f"- 群: {self._format_group_label(msg=msg)}",
                f"- 群员: {self._format_member_label(msg=msg)}",
                "",
                "### 引用内容",
                "",
                self._format_message_text(
                    msg=msg,
                    images_attached=images_attached,
                ),
            ]
        )

    def _format_group_label(self, *, msg: GroupMessage) -> str:
        """格式化群名称和群号。"""
        group_name = self._clean_text(msg.group_name)
        if group_name is None:
            return msg.group_id
        return f"{group_name} ({msg.group_id})"

    def _format_member_label(self, *, msg: GroupMessage) -> str:
        """格式化群员昵称、QQ 号和角色。"""
        display_name = self._clean_text(msg.sender.card)
        if display_name is None:
            display_name = self._clean_text(msg.sender.nickname)
        if display_name is None:
            display_name = "未知群员"
        role = self._format_role(msg.sender.role)
        return f"{display_name} ({msg.user_id}, {role})"

    def _format_role(self, role: str | None) -> str:
        """把 NapCat 群角色转换为中文标签。"""
        if role is None:
            return "未知角色"
        return ROLE_LABELS.get(role, role)

    def _format_message_text(self, *, msg: GroupMessage, images_attached: bool) -> str:
        """格式化消息正文，保留 AI 可理解的非文本消息段摘要。"""
        return self._format_segments_as_text(
            segments=msg.message,
            images_attached=images_attached,
            depth=0,
        )

    def _format_segments_as_text(
        self,
        *,
        segments: list[MessageSegment],
        images_attached: bool,
        depth: int,
    ) -> str:
        """把一组消息段转换为低噪音文本，并集中处理图片提示。"""
        text_parts: list[str] = []
        has_image = False
        for segment in segments:
            if isinstance(segment, Image):
                has_image = True
                continue
            formatted_segment = self._format_segment_text(
                segment=segment,
                depth=depth,
            )
            if formatted_segment is None:
                continue
            cleaned_segment = formatted_segment.strip()
            if cleaned_segment:
                text_parts.append(cleaned_segment)
        text = "\n\n".join(text_parts).strip()
        if has_image:
            image_notice = "（图片消息，图片已随本条输入提供）"
            if not images_attached:
                image_notice = "（消息包含图片，但当前请求未附带图片内容）"
            if text:
                return f"{text}\n\n{image_notice}"
            return image_notice
        if text:
            return text
        return "（无文本内容）"

    def _format_segment_text(
        self, *, segment: MessageSegment, depth: int
    ) -> str | None:
        """把单个非图片消息段转换为可读文本。"""
        if isinstance(segment, Text):
            return segment.data.text
        if isinstance(segment, (At, Reply)):
            return None
        if isinstance(segment, Share):
            return self._format_share_segment(segment=segment)
        if isinstance(segment, Forward):
            return self._format_forward_segment(segment=segment, depth=depth)
        if isinstance(segment, JsonSegment):
            return self._format_json_payload(label="JSON 卡片", payload=segment.data.data)
        if isinstance(segment, MFace):
            return self._format_mface_segment(segment=segment)
        if isinstance(segment, File):
            return self._format_file_segment(segment=segment)
        if isinstance(segment, Markdown):
            return self._format_markdown_segment(segment=segment)
        if isinstance(segment, LightApp):
            return self._format_light_app_segment(segment=segment)
        if isinstance(segment, MiniApp):
            return self._format_json_payload(
                label="小程序卡片",
                payload=segment.data.content,
            )
        if isinstance(segment, Node):
            return self._format_node_segment(segment=segment, depth=depth)
        if isinstance(segment, UnknownSegment):
            return self._format_unknown_segment(segment=segment)
        return f"（暂不支持的消息段: {segment.type}）"

    def _format_share_segment(self, *, segment: Share) -> str:
        """格式化链接分享消息段。"""
        fields: list[str] = []
        self._append_optional_field(fields=fields, label="标题", value=segment.data.title)
        self._append_optional_field(
            fields=fields, label="描述", value=segment.data.content
        )
        self._append_optional_field(fields=fields, label="链接", value=segment.data.url)
        self._append_optional_field(fields=fields, label="封面", value=segment.data.image)
        if not fields:
            return "（链接分享，未包含可读内容）"
        return "（链接分享）\n" + "\n".join(fields)

    def _format_forward_segment(self, *, segment: Forward, depth: int) -> str:
        """格式化合并转发消息段，优先展开已随事件携带的内容。"""
        header = f"（合并转发消息，ID: {segment.data.id}）"
        if segment.data.content is None:
            return f"{header}\n（未包含可展开内容）"
        if depth >= FORWARD_MAX_DEPTH:
            return f"{header}\n（合并转发展开达到深度上限，剩余内容已省略）"
        content = self._format_forward_content(
            value=segment.data.content,
            depth=depth + 1,
        )
        return f"{header}\n{content}"

    def _format_forward_content(self, *, value: JsonValue, depth: int) -> str:
        """把合并转发的内嵌内容转换为文本。"""
        if depth > FORWARD_MAX_DEPTH:
            return "（合并转发展开达到深度上限，剩余内容已省略）"
        parsed_segments = self._try_parse_message_segments(value=value)
        if parsed_segments is not None:
            return self._format_segments_as_text(
                segments=parsed_segments,
                images_attached=False,
                depth=depth,
            )
        if isinstance(value, list):
            if not value:
                return "（合并转发内容为空）"
            formatted_items: list[str] = []
            visible_items = value[:FORWARD_MAX_ITEMS]
            for index, item in enumerate(visible_items, start=1):
                item_text = self._format_forward_item(value=item, depth=depth)
                formatted_items.append(f"{index}. {item_text}")
            remaining_count = len(value) - FORWARD_MAX_ITEMS
            if remaining_count > 0:
                formatted_items.append(
                    f"（其余 {remaining_count} 条合并转发内容已省略）"
                )
            return "\n".join(formatted_items)
        if isinstance(value, dict):
            return self._format_forward_item(value=value, depth=depth)
        if isinstance(value, str):
            return self._truncate_text(text=value, limit=FIELD_TEXT_LIMIT)
        return self._format_json_value(value=value, limit=FIELD_TEXT_LIMIT)

    def _format_forward_item(self, *, value: JsonValue, depth: int) -> str:
        """格式化合并转发中的单条消息或节点。"""
        parsed_segment = self._try_parse_message_segment(value=value)
        if parsed_segment is not None:
            return self._format_segments_as_text(
                segments=[parsed_segment],
                images_attached=False,
                depth=depth,
            )
        if isinstance(value, dict):
            sender = self._format_forward_sender(payload=value)
            content = self._extract_forward_item_content(payload=value)
            if content is not None:
                content_text = self._format_forward_content(
                    value=content,
                    depth=depth + 1,
                )
                if sender is not None:
                    return f"{sender}: {content_text}"
                return content_text
            return self._format_card_object(label="合并转发条目", payload=value)
        if isinstance(value, list):
            return self._format_forward_content(value=value, depth=depth + 1)
        if isinstance(value, str):
            return self._truncate_text(text=value, limit=FIELD_TEXT_LIMIT)
        return self._format_json_value(value=value, limit=FIELD_TEXT_LIMIT)

    def _format_node_segment(self, *, segment: Node, depth: int) -> str:
        """格式化合并转发节点消息段。"""
        sender = self._format_node_sender(segment=segment)
        if segment.data.content is None:
            if segment.data.id is not None:
                return f"（转发节点，ID: {segment.data.id}）"
            return "（转发节点，未包含可读内容）"
        if isinstance(segment.data.content, str):
            content = self._truncate_text(
                text=segment.data.content,
                limit=FIELD_TEXT_LIMIT,
            )
        else:
            content = self._format_segments_as_text(
                segments=segment.data.content,
                images_attached=False,
                depth=depth + 1,
            )
        if sender is None:
            return f"（转发节点）\n{content}"
        return f"（转发节点，{sender}）\n{content}"

    def _format_json_payload(self, *, label: str, payload: JsonValue) -> str:
        """格式化 JSON 或卡片类负载。"""
        normalized_payload = self._normalize_card_payload(payload=payload)
        if isinstance(normalized_payload, dict):
            return self._format_card_object(label=label, payload=normalized_payload)
        if normalized_payload is None:
            return f"（{label}，未包含可读内容）"
        if isinstance(normalized_payload, list):
            summary = self._format_json_value(
                value=normalized_payload,
                limit=JSON_TEXT_LIMIT,
            )
            return f"（{label}）\n摘要: {summary}"
        text = self._truncate_text(
            text=str(normalized_payload),
            limit=FIELD_TEXT_LIMIT,
        )
        return f"（{label}）\n内容: {text}"

    def _format_card_object(self, *, label: str, payload: JsonObject) -> str:
        """从卡片对象中提取标题、描述和链接等可读字段。"""
        fields: list[str] = []
        title = self._find_first_text(
            value=payload,
            keys=("title", "name", "appName", "app_name", "app"),
        )
        description = self._find_first_text(
            value=payload,
            keys=("content", "desc", "description", "summary", "prompt", "text"),
        )
        url = self._find_first_text(
            value=payload,
            keys=("url", "jumpUrl", "jump_url", "link", "href"),
        )
        image = self._find_first_text(
            value=payload,
            keys=("image", "preview", "icon", "cover"),
        )
        self._append_optional_field(fields=fields, label="标题", value=title)
        self._append_optional_field(fields=fields, label="描述", value=description)
        self._append_optional_field(fields=fields, label="链接", value=url)
        self._append_optional_field(fields=fields, label="封面", value=image)
        if not fields:
            fields.append(
                "摘要: "
                + self._format_json_value(value=payload, limit=JSON_TEXT_LIMIT)
            )
        return f"（{label}）\n" + "\n".join(fields)

    def _format_mface_segment(self, *, segment: MFace) -> str:
        """格式化商城表情消息段。"""
        fields: list[str] = []
        self._append_optional_field(
            fields=fields, label="摘要", value=segment.data.summary
        )
        self._append_optional_field(
            fields=fields, label="表情 ID", value=segment.data.emoji_id
        )
        self._append_optional_field(
            fields=fields, label="表情包 ID", value=segment.data.emoji_package_id
        )
        self._append_optional_field(fields=fields, label="Key", value=segment.data.key)
        self._append_optional_field(fields=fields, label="链接", value=segment.data.url)
        return "（商城表情）\n" + "\n".join(fields)

    def _format_file_segment(self, *, segment: File) -> str:
        """格式化文件消息段。"""
        fields: list[str] = []
        file_name = self._clean_text(segment.data.name)
        if file_name is None:
            file_name = self._clean_text(segment.data.file)
        self._append_optional_field(fields=fields, label="文件名", value=file_name)
        self._append_optional_field(
            fields=fields, label="文件 ID", value=segment.data.file_id
        )
        self._append_optional_field(
            fields=fields,
            label="大小",
            value=self._format_file_size(size=segment.data.file_size),
        )
        if not fields:
            return "（文件消息，未包含可读内容）"
        return "（文件消息）\n" + "\n".join(fields)

    def _format_markdown_segment(self, *, segment: Markdown) -> str:
        """格式化 Markdown 消息段。"""
        content = self._clean_text(segment.data.content)
        if content is None:
            return "（Markdown 消息，内容为空）"
        return (
            "（Markdown 消息）\n"
            + self._truncate_text(text=content, limit=MARKDOWN_TEXT_LIMIT)
        )

    def _format_light_app_segment(self, *, segment: LightApp) -> str:
        """格式化小程序卡片消息段。"""
        payload: JsonObject = {}
        self._set_optional_payload_text(
            payload=payload, key="title", value=segment.data.title
        )
        description = segment.data.description
        if description is None:
            description = segment.data.desc
        self._set_optional_payload_text(
            payload=payload, key="description", value=description
        )
        self._set_optional_payload_text(payload=payload, key="url", value=segment.data.url)
        self._set_optional_payload_text(payload=payload, key="app", value=segment.data.app)
        nested_payload = segment.data.content
        if nested_payload is None:
            nested_payload = segment.data.data
        if nested_payload is None:
            return self._format_json_payload(label="小程序卡片", payload=payload)
        if not payload:
            return self._format_json_payload(label="小程序卡片", payload=nested_payload)
        payload["content"] = nested_payload
        return self._format_json_payload(label="小程序卡片", payload=payload)

    def _format_unknown_segment(self, *, segment: UnknownSegment) -> str:
        """格式化未显式支持的消息段，避免整段内容对 AI 不可见。"""
        if segment.data is None:
            return f"（暂不支持的消息段: {segment.type}）"
        content = self._format_json_value(value=segment.data, limit=JSON_TEXT_LIMIT)
        return f"（暂不支持的消息段: {segment.type}，内容: {content}）"

    def _normalize_card_payload(self, *, payload: JsonValue) -> JsonValue:
        """把 JSON 字符串卡片尽早收窄为项目内 JSON 值。"""
        if not isinstance(payload, str):
            return payload
        cleaned_payload = payload.strip()
        if cleaned_payload == "":
            return ""
        try:
            # json.loads 是动态协议边界，解析后立即收窄为项目内 JSON 值。
            parsed_payload = json.loads(cleaned_payload)
        except json.JSONDecodeError:
            return cleaned_payload
        try:
            return to_json_value(parsed_payload)
        except TypeError:
            return cleaned_payload

    def _try_parse_message_segments(
        self, *, value: JsonValue
    ) -> list[MessageSegment] | None:
        """尝试把原始 JSON 列表解析为消息段列表。"""
        if not isinstance(value, list):
            return None
        try:
            return self.segments_adapter.validate_python(value)
        except ValidationError:
            return None

    def _try_parse_message_segment(self, *, value: JsonValue) -> MessageSegment | None:
        """尝试把原始 JSON 对象解析为单个消息段。"""
        if not isinstance(value, dict) or "type" not in value:
            return None
        try:
            return self.segment_adapter.validate_python(value)
        except ValidationError:
            return None

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

    def _format_forward_sender(self, *, payload: JsonObject) -> str | None:
        """格式化合并转发条目的发送者信息。"""
        sender = payload.get("sender")
        name: str | None = None
        user_id: str | None = None
        if isinstance(sender, dict):
            name = self._find_first_text(
                value=sender,
                keys=("card", "nickname", "name"),
            )
            user_id = self._find_first_text(value=sender, keys=("user_id", "uin", "qq"))
        if name is None:
            name = self._find_first_text(value=payload, keys=("nickname", "name"))
        if user_id is None:
            user_id = self._find_first_text(value=payload, keys=("user_id", "uin", "qq"))
        if name is not None and user_id is not None:
            return f"{name} ({user_id})"
        if name is not None:
            return name
        if user_id is not None:
            return user_id
        return None

    def _format_node_sender(self, *, segment: Node) -> str | None:
        """格式化转发节点中的发送者信息。"""
        name = self._clean_text(segment.data.nickname)
        user_id = segment.data.user_id
        if name is not None and user_id is not None:
            return f"{name} ({user_id})"
        if name is not None:
            return name
        return user_id

    def _find_first_text(
        self, *, value: JsonValue, keys: tuple[str, ...], depth: int = 0
    ) -> str | None:
        """在卡片 JSON 中按候选键递归寻找第一个可读文本。"""
        if depth > 3:
            return None
        if isinstance(value, dict):
            for key in keys:
                if key not in value:
                    continue
                text = self._json_scalar_to_text(value=value[key])
                if text is not None:
                    return text
            for item in value.values():
                text = self._find_first_text(value=item, keys=keys, depth=depth + 1)
                if text is not None:
                    return text
        if isinstance(value, list):
            for item in value:
                text = self._find_first_text(value=item, keys=keys, depth=depth + 1)
                if text is not None:
                    return text
        return None

    def _json_scalar_to_text(self, *, value: JsonValue) -> str | None:
        """把 JSON 标量转换为可展示文本。"""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (str, int, float)):
            return self._clean_text(str(value))
        return None

    def _append_optional_field(
        self, *, fields: list[str], label: str, value: str | None
    ) -> None:
        """把非空字段追加为统一的中文行。"""
        cleaned_value = self._clean_text(value)
        if cleaned_value is None:
            return
        fields.append(
            f"{label}: {self._truncate_text(text=cleaned_value, limit=FIELD_TEXT_LIMIT)}"
        )

    def _set_optional_payload_text(
        self, *, payload: JsonObject, key: str, value: str | None
    ) -> None:
        """把非空文本写入卡片摘要负载。"""
        cleaned_value = self._clean_text(value)
        if cleaned_value is not None:
            payload[key] = cleaned_value

    def _format_json_value(self, *, value: JsonValue, limit: int) -> str:
        """把 JSON 值压缩成一行摘要。"""
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return self._truncate_text(text=text, limit=limit)

    def _format_file_size(self, *, size: int | None) -> str | None:
        """把文件字节数格式化为用户可读大小。"""
        if size is None:
            return None
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

    def _truncate_text(self, *, text: str, limit: int) -> str:
        """按固定上限截断长文本并标记截断状态。"""
        cleaned_text = text.strip()
        if len(cleaned_text) <= limit:
            return cleaned_text
        return f"{cleaned_text[:limit]}...（已截断）"

    def _has_image(self, *, msg: GroupMessage) -> bool:
        """判断群消息中是否包含图片段。"""
        return any(isinstance(segment, Image) for segment in msg.message)

    def _extract_reply_id(self, *, msg: GroupMessage) -> NapCatId | None:
        """提取当前消息引用的消息 ID。"""
        for segment in msg.message:
            if isinstance(segment, Reply):
                return segment.data.id
        return None

    def extract_mentions(self, *, msg: GroupMessage) -> list[NapCatId]:
        """提取群消息中的艾特对象。"""
        mentions: list[NapCatId] = []
        for segment in msg.message:
            if isinstance(segment, At) and segment.data.qq != "all":
                mentions.append(segment.data.qq)
        return mentions

    def _clean_text(self, value: str | None) -> str | None:
        """清理可选文本，空白文本统一视为缺失。"""
        if value is None:
            return None
        cleaned_value = value.strip()
        if cleaned_value == "":
            return None
        return cleaned_value

    def _current_time(self) -> str:
        """返回当前北京时间字符串。"""
        now = datetime.now(BEIJING_TIMEZONE)
        return now.strftime("%Y-%m-%d %H:%M:%S")
