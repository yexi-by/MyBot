"""基于 OpenAI Images 协议的群聊生图插件。"""

from collections.abc import Sequence
from typing import ClassVar, Final, override

from pydantic import Field

from app.config.plugin_config import load_plugin_config
from app.database import GroupDataScope, StoredGroupMessage
from app.models import (
    GroupMessage,
    Image,
    MessageSegment,
    NapCatId,
    Reply,
    StrictModel,
    Text,
)
from app.plugins.base import BasePlugin
from app.services import ChatMessage, NapCatImageReader, NapCatImageResource
from app.utils.log import log_event, log_exception

CONFIG_SECTION: Final[str] = "image_generate"
TEXT_IMAGE_TOKEN: Final[str] = "/生图"
HELP_TOKEN: Final[str] = "/help生图"
HELP_TEXT: Final[str] = """生图插件使用指南

文生图:
发送 /生图 图片描述

图生图:
发送图片并附带 /生图 修改要求
也可以回复一条包含图片的消息，再发送 /生图 修改要求

只带文字时走文生图；当前消息或引用消息里包含图片时走图生图。"""
CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 40


class ImageGenerateConfig(StrictModel):
    """生图插件配置。"""

    group_ids: list[NapCatId]
    model_name: str
    model_vendors: str
    image_fetch_concurrency: int = Field(default=4, ge=1)
    image_download_timeout_seconds: float = Field(default=15.0, gt=0)


class ImageGeneratePlugin(BasePlugin[GroupMessage]):
    """处理群聊中的文生图与图生图请求。"""

    plugin_id: ClassVar[str] = "image_generate"
    name: ClassVar[str] = "生图插件"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """读取生图插件配置。"""
        self.config: ImageGenerateConfig = load_plugin_config(
            section_name=CONFIG_SECTION,
            model_cls=ImageGenerateConfig,
        )
        self.group_ids: set[NapCatId] = set(self.config.group_ids)
        self.image_reader: NapCatImageReader = NapCatImageReader(
            bot=self.context.bot,
            http_client=self.context.direct_httpx,
            fetch_concurrency=self.config.image_fetch_concurrency,
            download_timeout_seconds=self.config.image_download_timeout_seconds,
        )

    @override
    async def run(self, msg: GroupMessage) -> bool:
        """解析群消息中的生图指令并发送生成结果。"""
        if msg.group_id not in self.group_ids:
            return False
        text = self._extract_plain_text(msg=msg)
        if text == HELP_TOKEN:
            _ = await self.context.bot.send_msg(
                group_id=msg.group_id,
                at=msg.user_id,
                text=HELP_TEXT,
            )
            return True
        prompt = self._extract_prompt(text=text)
        if prompt is None:
            return False
        if prompt == "":
            _ = await self.context.bot.send_msg(
                group_id=msg.group_id,
                at=msg.user_id,
                text="请在 /生图 后填写图片描述或修改要求。",
            )
            return True
        try:
            input_images = await self._collect_input_images(msg=msg)
        except Exception as exc:
            log_exception(
                event="image_generate.input_load_failed",
                category="plugin",
                message="生图插件读取输入图片失败",
                exc=exc,
                group_id=msg.group_id,
                user_id=msg.user_id,
            )
            _ = await self.context.bot.send_msg(
                group_id=msg.group_id,
                at=msg.user_id,
                text=f"读取图片失败: {exc}",
            )
            return True
        await self._send_status(msg=msg, image_count=len(input_images))
        await self._generate_and_send_image(
            group_id=msg.group_id,
            user_id=msg.user_id,
            prompt=prompt,
            images=input_images,
        )
        return True

    def _extract_plain_text(self, *, msg: GroupMessage) -> str:
        """提取群消息中的纯文本内容。"""
        text_parts = [
            segment.data.text for segment in msg.message if isinstance(segment, Text)
        ]
        return "".join(text_parts).strip()

    def _extract_prompt(self, *, text: str) -> str | None:
        """从文本中提取 /生图 后的提示词。"""
        if not text.startswith(TEXT_IMAGE_TOKEN):
            return None
        return text.removeprefix(TEXT_IMAGE_TOKEN).strip()

    async def _collect_input_images(self, *, msg: GroupMessage) -> list[bytes]:
        """收集当前消息和引用消息中的图生图输入图片。"""
        images = await self._collect_message_images(
            segments=msg.message,
            message_id=msg.message_id,
        )
        reply_message = await self._load_reply_message(msg=msg)
        if reply_message is not None:
            images.extend(
                await self._collect_message_images(
                    segments=reply_message.segments,
                    message_id=reply_message.message_id,
                )
            )
        return images

    async def _load_reply_message(
        self, *, msg: GroupMessage
    ) -> StoredGroupMessage | None:
        """读取当前消息引用的未撤回群消息。"""
        reply_id = self._extract_reply_id(msg=msg)
        if reply_id is None:
            return None
        return await self.context.group_messages.get_active(
            scope=GroupDataScope(
                bot_id=msg.self_id,
                group_id=msg.group_id,
            ),
            message_id=reply_id,
        )

    def _extract_reply_id(self, *, msg: GroupMessage) -> NapCatId | None:
        """提取当前消息引用的消息 ID。"""
        for segment in msg.message:
            if isinstance(segment, Reply):
                return segment.data.id
        return None

    async def _collect_message_images(
        self,
        *,
        segments: Sequence[MessageSegment],
        message_id: NapCatId,
    ) -> list[bytes]:
        """读取单条群消息中的所有图片。"""
        resources: list[NapCatImageResource] = []
        for segment_index, segment in enumerate(segments):
            if not isinstance(segment, Image):
                continue
            resources.append(
                NapCatImageResource(
                    label=f"消息 {message_id} 的第 {segment_index + 1} 个消息段",
                    file=segment.data.file,
                    file_id=segment.data.file_id,
                    path=segment.data.path,
                    url=segment.data.url,
                )
            )
        results = await self.image_reader.read_many(resources=resources)
        images: list[bytes] = []
        for result in results:
            if result.image_bytes is not None:
                images.append(result.image_bytes)
                continue
            raise ValueError(
                f"图片读取失败: {result.resource.label}: "
                f"{result.error or '图片没有可读取内容'}"
            )
        return images

    async def _send_status(self, *, msg: GroupMessage, image_count: int) -> None:
        """发送生图处理中的状态提醒。"""
        if image_count > 0:
            log_event(
                level="INFO",
                event="image_generate.requested",
                category="plugin",
                message="用户发起图生图",
                group_id=msg.group_id,
                user_id=msg.user_id,
                image_count=image_count,
            )
            status_text = "正在根据图片生成新图片..."
        else:
            log_event(
                level="INFO",
                event="image_generate.requested",
                category="plugin",
                message="用户发起文生图",
                group_id=msg.group_id,
                user_id=msg.user_id,
                image_count=image_count,
            )
            status_text = "正在生成图片..."
        _ = await self.context.bot.send_msg(
            group_id=msg.group_id,
            at=msg.user_id,
            text=status_text,
        )

    async def _generate_and_send_image(
        self,
        *,
        group_id: NapCatId,
        user_id: NapCatId,
        prompt: str,
        images: list[bytes],
    ) -> None:
        """调用 LLM 图片接口并发送生成结果。"""
        message = ChatMessage(
            role="user",
            text=prompt,
            image=images if images else None,
        )
        try:
            image_base64 = await self.context.llm.get_image(
                message=message,
                model=self.config.model_name,
                model_vendors=self.config.model_vendors,
            )
        except Exception as exc:
            log_exception(
                event="image_generate.call_failed",
                category="plugin",
                message="生图插件调用图片接口失败",
                exc=exc,
                group_id=group_id,
                user_id=user_id,
                model_name=self.config.model_name,
                model_vendors=self.config.model_vendors,
            )
            _ = await self.context.bot.send_msg(
                group_id=group_id,
                at=user_id,
                text=f"生图失败: {exc}",
            )
            return
        image_segment = Image.new(f"base64://{image_base64}")
        _ = await self.context.bot.send_msg(
            group_id=group_id,
            message_segment=[image_segment],
        )
