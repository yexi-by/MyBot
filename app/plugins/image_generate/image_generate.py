"""基于 OpenAI 兼容接口的群聊生图插件。"""

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings

from app.models import GroupMessage
from app.services.llm.schemas import ChatMessage
from app.utils import (
    download_content,
    extract_text_from_message,
    get_reply_image_paths,
    load_config_section,
    logger,
    parse_message_chain,
)
from app.utils.message_utils import get_reply_message_from_db

from ..base import BasePlugin

PLUGINS_CONFIG_PATH = "plugins_config/plugins.toml"
SHARED_GROUP_SECTION = "shared_group"
IMAGE_CONFIG_SECTION = "image_generate"
TEXT_IMAGE_TOKEN = "/生图"
HELP_TOKEN = "/help生图"
HELP_TEXT = """✨ 生图插件使用指南 ✨

🎨 文生图：
发送：/生图 [提示词]
示例：/生图 一只蹲在窗边的橘猫，清晨阳光，写实风格

🖼️ 图生图：
方式一：直接发送图片并附带 /生图 [提示词]
方式二：回复一条包含图片的消息，再发送 /生图 [提示词]

📌 规则：
只带文字时走文生图；
消息里有图片，或回复的消息里有图片时，走图生图。"""
CONSUMERS_COUNT = 5
PRIORITY = 40

ImageSize = Literal[
    "auto",
    "256x256",
    "512x512",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "1792x1024",
    "1024x1792",
]
ImageQuality = Literal["standard", "low", "medium", "high", "auto"]
InputFidelity = Literal["high", "low"]


class GroupConfig(BaseModel):
    """定义共享群配置。"""

    group_id: int


class SharedGroupConfig(BaseSettings):
    """定义共享群插件配置。"""

    group_config: list[GroupConfig]


class PluginConfig(BaseSettings):
    """定义生图插件配置。"""

    model_name: str
    model_vendors: str
    size: ImageSize | None = None
    quality: ImageQuality | None = None
    input_fidelity: InputFidelity | None = None


class ImageGeneratePlugin(BasePlugin[GroupMessage]):
    """处理群聊中的文生图与图生图请求。"""

    name = "生图插件"
    consumers_count = CONSUMERS_COUNT
    priority = PRIORITY

    def setup(self) -> None:
        """加载插件配置。"""
        shared_config = load_config_section(
            file_path=PLUGINS_CONFIG_PATH,
            section_name=SHARED_GROUP_SECTION,
            model_cls=SharedGroupConfig,
        )
        self.config = load_config_section(
            file_path=PLUGINS_CONFIG_PATH,
            section_name=IMAGE_CONFIG_SECTION,
            model_cls=PluginConfig,
        )
        self.group_list = [
            group_config.group_id for group_config in shared_config.group_config
        ]

    def _read_local_images(self, image_paths: list[str]) -> list[bytes]:
        """读取回复消息中的本地图片。"""
        return [Path(image_path).read_bytes() for image_path in image_paths]

    async def _collect_current_images(self, image_url_lst: list[str]) -> list[bytes]:
        """下载当前消息里直接附带的图片。"""
        if not image_url_lst:
            return []
        image_tasks = [
            download_content(url=image_url, client=self.context.direct_httpx)
            for image_url in image_url_lst
        ]
        images = await asyncio.gather(*image_tasks)
        return list(images)

    async def _collect_reply_images(
        self, reply_id: int | None, group_id: int
    ) -> list[bytes]:
        """读取被回复消息里的图片。"""
        if reply_id is None:
            return []

        reply_message = await get_reply_message_from_db(
            database=self.context.database,
            self_id=self.context.bot.boot_id,
            group_id=group_id,
            reply_id=reply_id,
        )
        if reply_message is None:
            return []

        image_paths = get_reply_image_paths(reply_message=reply_message)
        if not image_paths:
            return []
        return await asyncio.to_thread(self._read_local_images, image_paths)

    async def _collect_input_images(
        self,
        group_id: int,
        reply_id: int | None,
        image_url_lst: list[str],
    ) -> list[bytes]:
        """收集图生图所需的全部输入图片。"""
        current_images, reply_images = await asyncio.gather(
            self._collect_current_images(image_url_lst=image_url_lst),
            self._collect_reply_images(reply_id=reply_id, group_id=group_id),
        )
        return current_images + reply_images

    async def _generate_image(
        self,
        group_id: int,
        user_id: int,
        prompt: str,
        images: list[bytes],
    ) -> None:
        """调用统一 LLM 图像接口并回传结果。"""
        message = ChatMessage(
            role="user",
            text=prompt,
            image=images if images else None,
        )
        request_kwargs: dict[str, str] = {}
        if self.config.size is not None:
            request_kwargs["size"] = self.config.size
        if self.config.quality is not None:
            request_kwargs["quality"] = self.config.quality
        if images and self.config.input_fidelity is not None:
            request_kwargs["input_fidelity"] = self.config.input_fidelity

        try:
            image_base64 = await self.context.llm.get_image(
                message=message,
                model=self.config.model_name,
                model_vendors=self.config.model_vendors,
                **request_kwargs,
            )
        except Exception as exc:
            logger.exception("生图插件调用图像接口失败")
            await self.context.bot.send_msg(
                group_id=group_id,
                at=user_id,
                text=f"生图失败：{exc}",
            )
            return

        file_image_base = f"base64://{image_base64}"
        await self.context.bot.send_msg(group_id=group_id, image=file_image_base)

    async def run(self, msg: GroupMessage) -> bool:
        """处理群消息并执行文生图或图生图。"""
        if msg.group_id not in self.group_list:
            return False

        _, text_list, image_url_lst, reply_id = parse_message_chain(msg=msg)
        text = "".join(text_list).strip()

        if text == HELP_TOKEN:
            await self.context.bot.send_msg(
                group_id=msg.group_id,
                at=msg.user_id,
                text=HELP_TEXT,
            )
            return True

        prompt = extract_text_from_message(text=text, token=TEXT_IMAGE_TOKEN)
        if prompt is None:
            return False

        prompt = prompt.strip()
        if not prompt:
            await self.context.bot.send_msg(
                group_id=msg.group_id,
                at=msg.user_id,
                text="请在 /生图 后填写图片描述或修改要求。",
            )
            return True

        try:
            input_images = await self._collect_input_images(
                group_id=msg.group_id,
                reply_id=reply_id,
                image_url_lst=image_url_lst,
            )
        except Exception as exc:
            logger.exception("生图插件读取输入图片失败")
            await self.context.bot.send_msg(
                group_id=msg.group_id,
                at=msg.user_id,
                text=f"读取图片失败：{exc}",
            )
            return True

        if input_images:
            logger.info(
                f"用户 {msg.user_id} 在群 {msg.group_id} 发起图生图，请求图片数 {len(input_images)}"
            )
            status_text = "正在根据图片生成新图片……"
        else:
            logger.info(f"用户 {msg.user_id} 在群 {msg.group_id} 发起文生图")
            status_text = "正在生成图片……"

        await self.context.bot.send_msg(
            group_id=msg.group_id,
            at=msg.user_id,
            text=status_text,
        )
        await self._generate_image(
            group_id=msg.group_id,
            user_id=msg.user_id,
            prompt=prompt,
            images=input_images,
        )
        return True
