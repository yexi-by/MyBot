"""AI 群聊插件的 LLM 输入构造器。"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiofiles
import httpx

from app.database import RedisDatabaseManager
from app.models import At, GroupMessage, Image, NapCatId, Reply, Text
from app.services import ChatMessage
from app.utils.log import log_event

from .config import AIGroupChatConfig
from .constants import (
    BEIJING_TIMEZONE,
    ROLE_LABELS,
)


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
        """格式化消息正文，过滤对机器人的艾特并展示图片提示。"""
        text = self._extract_plain_text(msg=msg)
        has_image = self._has_image(msg=msg)
        image_notice = "（图片消息，图片已随本条输入提供）"
        if has_image and not images_attached:
            image_notice = "（消息包含图片，但当前请求未附带图片内容）"
        if text and has_image:
            return f"{text}\n\n{image_notice}"
        if text:
            return text
        if has_image:
            return image_notice
        return "（无文本内容）"

    def _has_image(self, *, msg: GroupMessage) -> bool:
        """判断群消息中是否包含图片段。"""
        return any(isinstance(segment, Image) for segment in msg.message)

    def _extract_plain_text(self, *, msg: GroupMessage) -> str:
        """提取消息中的纯文本内容。"""
        text_parts = [
            segment.data.text for segment in msg.message if isinstance(segment, Text)
        ]
        return "".join(text_parts).strip()

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
