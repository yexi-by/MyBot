from app.models import GroupMessage
from app.services import ContextHandler
from app.services.llm.schemas import ChatMessage
from app.utils import (
    extract_text_from_message,
    get_reply_image_paths,
    load_config,
    logger,
    parse_message_chain,
    parse_validated_json,
    pydantic_to_json_schema,
    read_files_content,
)
from app.utils.message_utils import get_reply_message_from_db
import traceback
from ..base import BasePlugin
from .segments import NaiImageKwargs, PluginConfig
from .utils import build_group_chat_contexts

# 配置文件路径
MAX_RETRY_ATTEMPTS = 5
GROUP_CONFIG_PATH = "plugins_config/nai_config.toml"
TEXT_IMAGE_TOKEN = "/生成图片"
HELP_TOKEN = "/help生图"
HELP_TEXT = f"""✨ AI生图使用指南 ✨

🎨 文生图:
发送: {TEXT_IMAGE_TOKEN} [提示词]
示例: {TEXT_IMAGE_TOKEN} 一个在海边散步的白发少女，蓝眼睛，唯美风格
说明: 你的提示词会经过LLM优化，可以使用自然语言描述，无需全是英文标签。

🖼️ 图生图:
回复图片发送: {TEXT_IMAGE_TOKEN} [提示词]
说明: 在回复中带上新的描述，AI会基于原图进行重绘。

💡 小贴士: 遇到生成失败会自动重试，重试时也是由LLM进行二次处理优化提示词。"""


class NaiImage(BasePlugin[GroupMessage]):
    name = "nai生图插件"
    consumers_count = 1  # 官网锁定并发1
    priority = 50

    def setup(self) -> None:
        config = load_config(file_path=GROUP_CONFIG_PATH, model_cls=PluginConfig)
        schema = pydantic_to_json_schema(NaiImageKwargs)
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
                logger.debug(f"nai生图插件llm生成提示词内容:{raw_response}")
                ai_response = parse_validated_json(raw_response, NaiImageKwargs)
                kwargs = ai_response.model_dump()
                image_base64 = await self.context.nai_client.generate_image(
                    image_base64=user_image_base64, width=1024, height=1024, **kwargs
                )
                file_image_base = f"base64://{image_base64}"
                await self.context.bot.send_msg(
                    group_id=msg.group_id, image=file_image_base
                )
                break
            except Exception:
                full_error = traceback.format_exc()
                logger.exception("处理过程中发生内部错误")
                error_message = ChatMessage(
                    role="user",
                    text=f"命令执行出错了，请根据以下堆栈信息修正输出:\n```python\n{full_error}\n```",
                )
                conversation_history.append(error_message)

    async def assemble_reply_message_details(self, reply_id: int, group_id: int):
        reply_message = await get_reply_message_from_db(
            database=self.context.database,
            self_id=self.context.bot.boot_id,
            group_id=group_id,
            reply_id=reply_id,
        )
        if not reply_message:
            return None
        image_path = get_reply_image_paths(reply_message=reply_message)
        if not image_path:
            logger.warning("找不到返回回复消息的图片路径,也有可能是消息里面没有图片")
            return None
        image_base64_list = read_files_content(
            file_paths=image_path, output_type="base64"
        )
        image_base64 = image_base64_list[0]  # novelai不支持单图
        return image_base64

    async def run(self, msg: GroupMessage) -> bool:
        image_base64 = None
        group_id = msg.group_id
        at = msg.user_id
        if group_id not in self.group_contexts:
            return False
        at_lst, text_list, image_url_lst, reply_id = parse_message_chain(msg=msg)
        text = "".join(text_list)
        if text == HELP_TOKEN:
            await self.context.bot.send_msg(group_id=group_id, at=at, text=HELP_TEXT)
            return True
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
