from app.models import GroupMessage
from app.services import ContextHandler
from app.services.llm.schemas import ChatMessage
from app.utils import convert_basemodel_to_schema, load_config, logger

from ..base import BasePlugin
from ..utils import (
    aggregate_messages,
    extract_text_from_message,
    find_replied_message_image_paths,
    get_response_images,
)
from .segments import NaiImageKwargs, PluginConfig
from .utils import build_group_chat_contexts

# 配置文件路径
MAX_RETRY_ATTEMPTS = 5
GROUP_CONFIG_PATH = "plugins_config/nai_config.toml"
TEXT_IMAGE_TOKEN = "/生图图片"


class NaiImage(BasePlugin[GroupMessage]):
    name = "ai生图插件"
    consumers_count = 1  # 官网锁定并发1
    priority = 6

    def setup(self) -> None:
        config = load_config(file_path=GROUP_CONFIG_PATH, model_cls=PluginConfig)
        schema = convert_basemodel_to_schema(NaiImageKwargs)
        self.group_contexts = build_group_chat_contexts(config=config, schema=schema)

    async def send_ai_message(
        self,
        msg: GroupMessage,
        prompt: str,
        chat_handler: ContextHandler,
        user_image_base64: str | None,
    ) -> None:
        conversation_history = chat_handler.messages_lst
        user_prompt = ChatMessage(role="user", text=prompt)
        conversation_history.append(user_prompt)
        attempt_count = 0
        while attempt_count <= MAX_RETRY_ATTEMPTS:
            attempt_count += 1
            raw_response = await self.context.llm.get_ai_text_response(
                messages=conversation_history,
                model_name="gemini-3-pro-preview",
                model_vendors="google",
            )
            try:
                ai_response = NaiImageKwargs.model_validate_json(raw_response)
                kwargs = ai_response.model_dump()
                image_base64 = await self.context.nai_client.generate_image(
                    image_base64=user_image_base64, width=1024, height=1024, **kwargs
                )
                file_image_base = f"base64://{image_base64}"
                await self.context.bot.send_msg(
                    group_id=msg.group_id, image=file_image_base
                )
                break
            except Exception as e:
                error_message = ChatMessage(role="user", text=f"出错了:\n{e}")
                conversation_history.append(error_message)

    async def assemble_reply_message_details(self, reply_id: int, group_id: int):
        reply_message = await self.context.database.search_messages(
            self_id=self.context.bot.boot_id,
            group_id=group_id,
            message_id=reply_id,
        )
        if not reply_message:
            logger.warning(
                f"redis没有查到数据,请检查群号 {group_id} ,被回复的消息id: {reply_id} 是否在数据库中"
            )
            return None
        image_path = find_replied_message_image_paths(reply_message=reply_message)
        if not image_path:
            logger.warning("找不到返回回复消息的图片路径,也有可能是消息里面没有图片")
            return None
        image_base64_list = get_response_images(
            image_path=image_path, output_type="base64"
        )
        image_base64 = image_base64_list[0]  # novelai不支持单图
        return image_base64

    async def run(self, msg: GroupMessage) -> bool:
        image_base64 = None
        group_id = msg.group_id
        at = msg.user_id
        if group_id not in self.group_contexts:
            return False
        at_lst, text_list, image_url_lst, reply_id = aggregate_messages(msg=msg)
        text = "".join(text_list)
        prompt = extract_text_from_message(text=text, token=TEXT_IMAGE_TOKEN)
        if prompt is None:
            return False
        await self.context.bot.send_msg(
            group_id=group_id, at=at, text="正在生成图片...."
        )
        if reply_id:
            image_base64 = await self.assemble_reply_message_details(
                reply_id=reply_id, group_id=msg.group_id
            )
            if not image_base64:
                await self.context.bot.send_msg(
                    group_id=group_id, at=at, text="被回复的图片为空"
                )
                return True
        chat_handler = self.group_contexts[msg.group_id]
        await self.send_ai_message(
            msg=msg,
            prompt=prompt,
            chat_handler=chat_handler,
            user_image_base64=image_base64,
        )
        return True
