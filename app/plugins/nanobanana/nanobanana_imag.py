import asyncio
from app.models import GroupMessage
from ..base import BasePlugin
from pydantic_settings import BaseSettings
from app.services.llm.schemas import ChatMessage
from pydantic import BaseModel
from app.utils import (
    extract_text_from_message,
    load_config,
    parse_message_chain,
    get_reply_image_paths,
    read_files_content,
    logger,
)
from app.utils.message_utils import get_reply_message_from_db

# 常量定义
GROUP_CONFIG_PATH = "plugins_config/nanobanana_config.toml"
TEXT_IMAGE_TOKEN = "/香蕉生图"
ADD_IMAGE_TOKEN = "/香蕉添加图片"
FINISH_IMAGE_TOKEN = "/香蕉添加完毕"
HELP_TOKEN = "/help香蕉生图"
TIMEOUT_SECONDS = 300
MODEL_NAME = "gemini-3-pro-image-4k"
MODEL_VENDOR = "Antigravity"
ERROR_TEXT = "生图失败,错误信息:{e}"
HELP_TEXT = f"""✨ 香蕉生图插件使用指南 ✨

🎨 文生图/单图生图:
发送: {TEXT_IMAGE_TOKEN} [提示词]
说明: 直接发送提示词进行文生图。若回复包含图片的消息，则进行单图生图。

🖼️ 多图生图:
1. 回复图片发送: {ADD_IMAGE_TOKEN}
   说明: 将回复的图片添加到任务队列。
2. 发送: {FINISH_IMAGE_TOKEN} [提示词]
   说明: 结束添加并开始生成。可顺带回复最后一张图片。

⏳ 超时说明: 任务创建后 {TIMEOUT_SECONDS} 秒内未完成将被自动清理。
"""

# 插件配置
CONSUMERS_COUNT = 2
PRIORITY = 20


class GroupConfig(BaseModel):
    group_id: int


class PluginConfig(BaseSettings):
    group_config: list[GroupConfig]


class BananaImage(BasePlugin[GroupMessage]):
    name = "banana生图插件"
    consumers_count = CONSUMERS_COUNT
    priority = PRIORITY

    def setup(self) -> None:
        self.config = load_config(file_path=GROUP_CONFIG_PATH, model_cls=PluginConfig)
        self.group_list = [
            group_config.group_id for group_config in self.config.group_config
        ]
        # 存储待处理的图片任务，键为 (group_id, user_id)，值为图片字节列表
        self.image_tasks: dict[tuple[int, int], list[bytes]] = {}

    async def _timeout_check(self, key: tuple[int, int]) -> None:
        """后台协程：检查任务是否超时"""
        await asyncio.sleep(TIMEOUT_SECONDS)
        if key in self.image_tasks:
            del self.image_tasks[key]
            logger.info(f"任务 {key} 已超时并被清理")

    async def _get_reply_images(
        self, reply_id: int | None, group_id: int
    ) -> list[bytes]:
        """从回复消息中获取图片"""
        if not reply_id:
            return []

        reply_message = await get_reply_message_from_db(
            database=self.context.database,
            self_id=self.context.bot.boot_id,
            group_id=group_id,
            reply_id=reply_id,
        )
        if not reply_message:
            logger.warning(f"未找到回复消息: {reply_id}")
            return []

        image_paths = get_reply_image_paths(reply_message=reply_message)
        if not image_paths:
            logger.warning(f"回复消息 {reply_id} 中不包含图片")
            return []

        return read_files_content(file_paths=image_paths, output_type="bytes")

    async def get_nanobanana_image(
        self, prompt: str, group_id: int, images: list[bytes] | None = None
    ) -> None:
        """调用 LLM 生成图片"""
        message = ChatMessage(role="user", text=prompt, image=images)
        try:
            image_base64 = await self.context.llm.get_image(
                message=message, model=MODEL_NAME, model_vendors=MODEL_VENDOR
            )
            file_image_base = f"base64://{image_base64}"
            await self.context.bot.send_msg(group_id=group_id, image=file_image_base)
        except Exception as e:
            error_text = ERROR_TEXT.format(e=e)
            await self.context.bot.send_msg(group_id=group_id, text=error_text)

    async def _handle_add_image(
        self, reply_id: int | None, group_id: int, user_id: int
    ) -> bool:
        """处理添加图片指令"""
        if not reply_id:
            logger.warning(f"用户 {user_id} 尝试添加图片但未回复消息")
            return False

        new_images = await self._get_reply_images(reply_id, group_id)
        if not new_images:
            return False

        key = (group_id, user_id)
        if key not in self.image_tasks:
            self.image_tasks[key] = []
            asyncio.create_task(self._timeout_check(key))
            logger.info(f"用户 {user_id} 创建了新的多图任务")

        self.image_tasks[key].extend(new_images)
        await self.context.bot.send_msg(
            group_id=group_id, at=user_id, text="图片添加成功"
        )
        return True

    async def _handle_finish_image(
        self, text: str, reply_id: int | None, group_id: int, user_id: int
    ) -> bool:
        """处理添加完毕指令"""
        prompt = extract_text_from_message(text=text, token=FINISH_IMAGE_TOKEN)
        if not prompt:
            return False

        current_images = []
        if reply_id:
            current_images = await self._get_reply_images(reply_id, group_id)

        key = (group_id, user_id)
        stored_images = self.image_tasks.get(key, [])
        all_images = stored_images + current_images

        logger.info(f"用户 {user_id} 结束多图任务，共 {len(all_images)} 张图片")

        await self.context.bot.send_msg(
            group_id=group_id, at=user_id, text="正在生成图片...."
        )

        await self.get_nanobanana_image(
            prompt=prompt, group_id=group_id, images=all_images if all_images else None
        )

        if key in self.image_tasks:
            del self.image_tasks[key]
        return True

    async def _handle_single_generate(
        self, text: str, reply_id: int | None, group_id: int, user_id: int
    ) -> bool:
        """处理单次生图指令"""
        prompt = extract_text_from_message(text=text, token=TEXT_IMAGE_TOKEN)
        if not prompt:
            return False

        images = None
        if reply_id:
            images = await self._get_reply_images(reply_id, group_id)

        logger.info(
            f"用户 {user_id} 请求生图，图片数量: {len(images) if images else 0}"
        )
        await self.context.bot.send_msg(
            group_id=group_id, at=user_id, text="正在生成图片...."
        )
        await self.get_nanobanana_image(prompt=prompt, group_id=group_id, images=images)
        return True

    async def run(self, msg: GroupMessage) -> bool:
        if msg.group_id not in self.group_list:
            return False

        at_lst, text_list, image_url_lst, reply_id = parse_message_chain(msg=msg)
        text = "".join(text_list).strip()

        match text:
            case t if t == HELP_TOKEN:
                await self.context.bot.send_msg(
                    group_id=msg.group_id, at=msg.user_id, text=HELP_TEXT
                )
                return True
            case t if t == ADD_IMAGE_TOKEN:
                return await self._handle_add_image(reply_id, msg.group_id, msg.user_id)
            case t if FINISH_IMAGE_TOKEN in t:
                return await self._handle_finish_image(
                    t, reply_id, msg.group_id, msg.user_id
                )
            case t if TEXT_IMAGE_TOKEN in t:
                return await self._handle_single_generate(
                    t, reply_id, msg.group_id, msg.user_id
                )
            case _:
                return False
