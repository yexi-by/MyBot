"""AI 群聊插件的 LLM 输入构造器。"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
import json

import httpx
from pydantic import TypeAdapter, ValidationError

from app.config import AIGroupChatConfig
from app.database import GroupDataScope, GroupMessageReader, StoredGroupMessage
from app.models import (
    Forward,
    GroupMessage,
    Image,
    JsonValue,
    MessageSegment,
    NapCatId,
    Node,
    Reply,
    Video,
)
from app.services import (
    ChatMessage,
    NapCatImageReader,
    NapCatImageResource,
)
from app.services.llm.tools import LLMImageArtifact, LLMImageError, LLMImageItem
from app.services.napcat.message_formatter import NapCatMessageTextFormatter
from app.services.napcat.video_reader import (
    NapCatMediaBot, NapCatVideoReader, NapCatVideoResource,
)
from app.utils.log import log_event

from .constants import (
    BEIJING_TIMEZONE,
    ROLE_LABELS,
)


@dataclass(frozen=True)
class BuiltTurnMessages:
    """描述本轮构造出的用户输入和图片读取结果。"""

    turn_messages: list[ChatMessage]
    image_items: list[LLMImageItem]
    truncated_image_count: int
    question: str

    @property
    def image_artifacts(self) -> list[LLMImageArtifact]:
        """按消息顺序返回成功读取的图片。"""
        return [item for item in self.image_items if isinstance(item, LLMImageArtifact)]

    @property
    def image_errors(self) -> list[LLMImageError]:
        """按消息顺序返回图片读取错误。"""
        return [item for item in self.image_items if isinstance(item, LLMImageError)]

    @property
    def detected_image_count(self) -> int:
        """返回截断前检测到的图片总数。"""
        return len(self.image_items) + self.truncated_image_count

    @property
    def loaded_image_count(self) -> int:
        """返回成功读取的图片数。"""
        return len(self.image_artifacts)


class GroupChatMessageBuilder:
    """把 NapCat 群消息转换为适合 LLM 阅读的输入消息。"""

    def __init__(
        self,
        *,
        config: AIGroupChatConfig,
        group_messages: GroupMessageReader,
        bot: NapCatMediaBot,
        http_client: httpx.AsyncClient,
    ) -> None:
        """保存构造 LLM 输入所需的服务。"""
        self.config: AIGroupChatConfig = config
        self.group_messages: GroupMessageReader = group_messages
        self.image_reader: NapCatImageReader = NapCatImageReader(
            bot=bot,
            http_client=http_client,
            fetch_concurrency=config.images.fetch_concurrency,
            download_timeout_seconds=config.images.download_timeout_seconds,
            max_image_bytes=(
                None
                if config.images.oversize_behavior == "describe"
                else config.images.max_image_bytes or None
            ),
        )
        self.video_reader = NapCatVideoReader(
            bot=bot,
            http_client=http_client,
            fetch_concurrency=config.videos.fetch_concurrency,
            download_timeout_seconds=config.videos.download_timeout_seconds,
            max_video_bytes=config.videos.max_video_bytes,
        )
        formatting = config.formatting
        self.message_formatter: NapCatMessageTextFormatter = NapCatMessageTextFormatter(
            field_text_limit=formatting.field_text_limit,
            json_text_limit=formatting.json_text_limit,
            markdown_text_limit=formatting.markdown_text_limit,
            forward_max_items=formatting.forward_max_items,
            forward_max_depth=formatting.forward_max_depth,
            nested_text_search_max_depth=formatting.nested_text_search_max_depth,
        )
        self.segment_adapter: TypeAdapter[MessageSegment] = TypeAdapter(MessageSegment)
        self.segments_adapter: TypeAdapter[list[MessageSegment]] = TypeAdapter(
            list[MessageSegment]
        )

    async def build_turn_messages(
        self,
        *,
        msg: GroupMessage,
    ) -> BuiltTurnMessages:
        """构造本轮提交给 LLM 的用户消息。"""
        reply_message = await self._load_reply_context_message(msg=msg)
        current_resources = self._build_image_resources(
            segments=msg.message,
            source_label="当前消息",
        )
        reply_resources = (
            self._build_image_resources(
                segments=reply_message.segments,
                source_label="引用消息",
            )
            if reply_message is not None
            else []
        )
        all_resources = [*current_resources, *reply_resources]
        truncated_image_count = 0
        read_results = await self.image_reader.read_many(resources=all_resources)
        image_items: list[LLMImageItem] = []
        loaded_sources: set[str] = set()
        for result_index, result in enumerate(read_results):
            source = (
                "current_message"
                if result_index < len(current_resources)
                else "quoted_message"
            )
            if result.image_bytes is not None:
                loaded_sources.add(source)
                image_items.append(
                    LLMImageArtifact(
                        label=result.resource.label,
                        image_bytes=result.image_bytes,
                    )
                )
                continue
            image_items.append(
                LLMImageError(
                    label=result.resource.label,
                    error_type=result.error_type or "ImageContentUnavailable",
                    error=result.error or "图片没有可读取内容",
                )
            )
        user_message = self._build_user_message_text(
            msg=msg,
            reply_message=reply_message,
            current_images_available="current_message" in loaded_sources,
            reply_images_available="quoted_message" in loaded_sources,
        )
        video_messages = await self._build_video_messages(
            msg=msg, reply_message=reply_message
        ) if self.config.videos.enabled else []
        return BuiltTurnMessages(
            turn_messages=[user_message, *video_messages],
            image_items=image_items,
            truncated_image_count=truncated_image_count,
            question=self._format_message_text(msg=msg, images_attached=True),
        )

    def _build_user_message_text(
        self,
        *,
        msg: GroupMessage,
        reply_message: StoredGroupMessage | None,
        current_images_available: bool,
        reply_images_available: bool,
    ) -> ChatMessage:
        """构造不直接携带图片字节的用户消息。"""
        text_blocks = [
            self._format_current_message_markdown(
                msg=msg,
                images_attached=current_images_available,
            )
        ]
        if reply_message is not None:
            text_blocks.append(
                self._format_quoted_message_markdown(
                    msg=reply_message,
                    images_attached=reply_images_available,
                )
            )
        return ChatMessage(
            role="user",
            text="\n\n".join(text_blocks),
        )

    def _build_image_resources(
        self, *, segments: Sequence[MessageSegment], source_label: str
    ) -> list[NapCatImageResource]:
        """按消息段顺序生成来源明确的图片资源。"""
        images = [item for item in self._iter_media(segments) if isinstance(item, Image)]
        return [NapCatImageResource(
            label=f"{source_label}第 {index} 张图片",
            file=item.data.file, file_id=item.data.file_id,
            path=item.data.path, url=item.data.url,
        ) for index, item in enumerate(images, 1)]

    async def _build_video_messages(
        self, *, msg: GroupMessage, reply_message: StoredGroupMessage | None
    ) -> list[ChatMessage]:
        """读取当前和引用视频，把部分失败与数量限制明确告知模型。"""
        resources: list[NapCatVideoResource] = []
        for label, segments in (
            ("当前消息", msg.message),
            ("引用消息", reply_message.segments if reply_message else ()),
        ):
            videos = [item for item in self._iter_media(segments) if isinstance(item, Video)]
            resources.extend(NapCatVideoResource(
                label=f"{label}第 {index} 个视频",
                file=item.data.file, file_id=item.data.file_id,
                path=item.data.path, url=item.data.url,
            ) for index, item in enumerate(videos, 1))
        limit = self.config.videos.max_per_turn
        selected = resources[:limit] if limit else resources
        results = await self.video_reader.read_many(resources=selected)
        messages: list[ChatMessage] = []
        errors: list[dict[str, str]] = []
        for result in results:
            if result.video_bytes is not None:
                messages.append(ChatMessage(
                    role="user", text=f"{result.resource.label}（附件仅在本轮可见）",
                    video=[result.video_bytes],
                ))
            else:
                errors.append({
                    "label": result.resource.label,
                    "error_type": result.error_type or "VideoContentUnavailable",
                    "error": result.error or "视频没有可读取内容",
                })
        truncated = len(resources) - len(selected)
        if errors or truncated:
            messages.append(ChatMessage(role="user", text=json.dumps({
                "resource_type": "video", "ok": not errors,
                "is_error": bool(errors), "errors": errors,
                "loaded_count": len(messages), "truncated_count": truncated,
                "message": "请根据已读取的内容回答；未读取的视频内容不可见。",
            }, ensure_ascii=False)))
        return messages

    def _iter_media(self, segments: Sequence[MessageSegment]) -> Iterator[Image | Video]:
        """遍历消息、节点和已内嵌转发中的图片与视频。"""
        for segment in segments:
            if isinstance(segment, (Image, Video)):
                yield segment
            elif isinstance(segment, Node) and isinstance(segment.data.content, list):
                yield from self._iter_media(segment.data.content)
            elif isinstance(segment, Forward) and segment.data.content is not None:
                yield from self._iter_json_media(segment.data.content)

    def _iter_json_media(self, value: JsonValue) -> Iterator[Image | Video]:
        """从合并转发的内嵌 JSON 形态中寻找媒体段。"""
        if isinstance(value, list):
            try:
                segments = self.segments_adapter.validate_python(value)
            except ValidationError:
                for item in value:
                    yield from self._iter_json_media(item)
            else:
                yield from self._iter_media(segments)
        elif isinstance(value, dict):
            if "type" in value:
                try:
                    segment = self.segment_adapter.validate_python(value)
                except ValidationError:
                    return
                yield from self._iter_media([segment])
            else:
                for key in ("message", "content", "messages", "data"):
                    if (nested := value.get(key)) is not None:
                        yield from self._iter_json_media(nested)

    async def _load_reply_context_message(
        self, *, msg: GroupMessage
    ) -> StoredGroupMessage | None:
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
            message="开始读取引用消息上下文",
            group_id=msg.group_id,
            message_id=msg.message_id,
            reply_message_id=reply_id,
        )
        stored_message = await self.group_messages.get_active(
            scope=GroupDataScope(
                bot_id=msg.self_id,
                group_id=msg.group_id,
            ),
            message_id=reply_id,
        )
        if stored_message is None:
            log_event(
                level="DEBUG",
                event="ai_group_chat.reply_context.missing",
                category="plugin",
                message="没有找到可读取的引用消息上下文",
                group_id=msg.group_id,
                message_id=msg.message_id,
                reply_message_id=reply_id,
            )
            return None
        log_event(
            level="DEBUG",
            event="ai_group_chat.reply_context.loaded",
            category="plugin",
            message="已读取引用消息上下文",
            group_id=msg.group_id,
            message_id=msg.message_id,
            reply_message_id=reply_id,
            reply_user_id=stored_message.sender_id,
            reply_segment_count=len(stored_message.segments),
        )
        return stored_message

    def _format_current_message_markdown(
        self, *, msg: GroupMessage, images_attached: bool
    ) -> str:
        """把当前群消息格式化为低噪音 Markdown。"""
        return "\n".join(
            [
                "## 当前消息",
                "",
                f"- 消息 ID: {msg.message_id}",
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
        self, *, msg: StoredGroupMessage, images_attached: bool
    ) -> str:
        """把被引用的历史消息格式化为 Markdown。"""
        return "\n".join(
            [
                "## 引用消息",
                "",
                "<small>注意：下面内容是本次发言引用的历史消息，只用于理解上下文，不是用户这次真正说的正文。</small>",
                "",
                f"- 消息 ID: {msg.message_id}",
                f"- 群: {self._format_group_identity(group_id=msg.scope.group_id, group_name=msg.group_name)}",
                f"- 群员: {self._format_stored_member_label(msg=msg)}",
                "",
                "### 引用内容",
                "",
                self._format_segments(
                    segments=msg.segments,
                    images_attached=images_attached,
                ),
            ]
        )

    def _format_group_label(self, *, msg: GroupMessage) -> str:
        """格式化群名称和群号。"""
        return self._format_group_identity(
            group_id=msg.group_id,
            group_name=msg.group_name,
        )

    def _format_group_identity(
        self, *, group_id: NapCatId, group_name: str | None
    ) -> str:
        """格式化群名称和群号。"""
        group_name = self._clean_text(group_name)
        if group_name is None:
            return group_id
        return f"{group_name} ({group_id})"

    def _format_member_label(self, *, msg: GroupMessage) -> str:
        """格式化群员昵称、QQ 号和角色。"""
        display_name = self._clean_text(msg.sender.card)
        if display_name is None:
            display_name = self._clean_text(msg.sender.nickname)
        if display_name is None:
            display_name = "未知群员"
        role = self._format_role(msg.sender.role)
        return f"{display_name} ({msg.user_id}, {role})"

    def _format_stored_member_label(self, *, msg: StoredGroupMessage) -> str:
        """格式化已持久化消息的群员身份。"""
        display_name = self._clean_text(msg.sender_name) or "未知群员"
        role = self._format_role(msg.sender_role)
        return f"{display_name} ({msg.sender_id}, {role})"

    def _format_role(self, role: str | None) -> str:
        """把 NapCat 群角色转换为中文标签。"""
        if role is None:
            return "未知角色"
        return ROLE_LABELS.get(role, role)

    def _format_message_text(self, *, msg: GroupMessage, images_attached: bool) -> str:
        """格式化消息正文，保留 AI 可理解的非文本消息段摘要。"""
        return self._format_segments(
            segments=msg.message,
            images_attached=images_attached,
        )

    def _format_segments(
        self, *, segments: Sequence[MessageSegment], images_attached: bool
    ) -> str:
        """格式化一组有序消息段。"""
        return self.message_formatter.format_segments(
            segments=list(segments),
            images_attached=images_attached,
            include_at=False,
            include_reply=False,
            include_image_details=False,
        )

    def _extract_reply_id(self, *, msg: GroupMessage) -> NapCatId | None:
        """提取当前消息引用的消息 ID。"""
        for segment in msg.message:
            if isinstance(segment, Reply):
                return segment.data.id
        return None

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
