from firecrawl import AsyncFirecrawlApp
from pydantic import TypeAdapter

from app.models import GroupMessage, MessageSegment
from app.services import ContextHandler
from app.services.llm.schemas import ChatMessage
from app.utils import convert_basemodel_to_schema, download_image, load_config, logger

from ..base import BasePlugin
from ..utils import (
    aggregate_messages,
    find_replied_message_image_paths,
    get_response_images,
)
from .segments import AIResponse, PluginConfig
from .utils import (
    build_group_chat_contexts,
    build_message_components,
    get_firecrawl_response,
)

# 配置文件路径
GROUP_CONFIG_PATH = "plugins_config/group_config.toml"

# 最大重试次数常量
MAX_RETRY_ATTEMPTS = 10
HELP_TOKEN = "/help对话"
HELP_TEXT = """✨ AI助手使用指南 ✨

💬 基础对话:
发送: @机器人 [你的问题]
说明: 直接与我对话，我会结合上下文进行回复。

🌐 联网能力:
说明: 我可以调用搜索工具(Firecrawl)获取最新信息，当你询问新闻或实时信息时，我会自动搜索。

🖼️ 多模态交互:
发送: @机器人 [图片] [问题]
说明: 可以发送图片给我，或者引用图片进行提问，我能看懂图片内容哦。

💡 智能特性:
你的每一句话都会被我认真思考(LLM处理)，我会根据需要决定是否联网搜索或直接回答。"""


class AIResponsePlugin(BasePlugin[GroupMessage]):
    name = "ai回复插件"
    consumers_count = 5
    priority = 5

    def setup(self) -> None:
        config = load_config(file_path=GROUP_CONFIG_PATH, model_cls=PluginConfig)
        schema = convert_basemodel_to_schema(AIResponse)
        self.group_contexts = build_group_chat_contexts(config=config, schema=schema)
        self.firecrawl_client = AsyncFirecrawlApp(
            api_key=config.firecrawl_config.api_key,
            api_url=config.firecrawl_config.api_url,
        )

    async def assemble_user_message(
        self, msg: GroupMessage, image_url_lst: list[str]
    ) -> ChatMessage:
        image_bytes_lst: list[bytes] = []
        for url in image_url_lst:
            image_bytes = await download_image(
                url=url, client=self.context.direct_httpx
            )
            image_bytes_lst.append(image_bytes)
        text_str = msg.model_dump_json(indent=2)
        text = f"以下是用户发言:\n{text_str}"
        chat_message = ChatMessage(
            role="user", image=image_bytes_lst if image_bytes_lst else None, text=text
        )
        return chat_message

    async def assemble_reply_message_details(
        self, reply_id: int, group_id: int
    ) -> ChatMessage | None:
        image_bytes_list = None
        self_id = self.context.bot.boot_id
        reply_message = await self.context.database.search_messages(
            self_id=self_id, group_id=group_id, message_id=reply_id
        )
        if not reply_message:
            logger.warning(
                f"redis没有查到数据,请检查群号 {group_id} ,被回复的消息id: {reply_id} 是否在数据库中"
            )
            return None
        image_path = find_replied_message_image_paths(reply_message=reply_message)
        if image_path:
            image_bytes_list = get_response_images(
                image_path=image_path, output_type="bytes"
            )
        text_str = reply_message.model_dump_json(indent=2)
        text = f"以下是用户回复的那条消息的详情:\n{text_str}"
        chat_message = ChatMessage(role="user", image=image_bytes_list, text=text)
        return chat_message

    async def async_chat_handler(
        self,
        chat_message_lst: list[ChatMessage],
        chat_handler: ContextHandler,
        group_id: int,
    ) -> None:
        conversation_history = chat_handler.messages_lst
        conversation_history.extend(chat_message_lst)
        attempt_count = 0
        chat_handler.build_chatmessage(message_lst=chat_message_lst)
        while attempt_count <= MAX_RETRY_ATTEMPTS:
            attempt_count += 1
            raw_response = await self.context.llm.get_ai_text_response(
                messages=conversation_history,
                model_name="gemini-3-pro-preview",
                model_vendors="google",
            )
            logger.debug(raw_response)
            try:
                ai_response = AIResponse.model_validate_json(raw_response)
                assistant_message = ChatMessage(role="assistant", text=raw_response)
                conversation_history.append(assistant_message)
                message_to_send = ai_response.send_message
                firecrawl_request = ai_response.firecrawl
                if message_to_send:
                    message_segments = build_message_components(
                        send_message=message_to_send
                    )
                    await self.context.bot.send_msg(
                        group_id=group_id, message_segment=message_segments
                    )
                    adapter = TypeAdapter(list[MessageSegment])
                    serialized_segments = adapter.dump_json(message_segments).decode(
                        "utf-8"
                    )
                    chat_handler.build_chatmessage(
                        role="assistant", text=serialized_segments
                    )
                if firecrawl_request:
                    tool_response = await get_firecrawl_response(
                        firecrawl=firecrawl_request, client=self.firecrawl_client
                    )
                    tool_output_message = ChatMessage(
                        role="user", text=f"工具输出:\n{tool_response}"
                    )
                    conversation_history.append(tool_output_message)
                if ai_response.end:
                    break
            except Exception as e:
                error_message = ChatMessage(role="user", text=f"出错了:\n{e}")
                conversation_history.append(error_message)

    async def run(self, msg: GroupMessage) -> bool:
        if msg.group_id not in self.group_contexts:
            return False
        at_lst, text_list, image_url_lst, reply_id = aggregate_messages(msg=msg)
        text = "".join(text_list).strip()
        if text == HELP_TOKEN:
            await self.context.bot.send_msg(
                group_id=msg.group_id, at=msg.user_id, text=HELP_TEXT
            )
            return True

        if self.context.bot.boot_id not in at_lst:
            return False
        chat_message_lst: list[ChatMessage] = []
        user_chat_message = await self.assemble_user_message(
            msg=msg, image_url_lst=image_url_lst
        )
        chat_message_lst.append(user_chat_message)
        if reply_id:
            reply_chat_message = await self.assemble_reply_message_details(
                reply_id=reply_id, group_id=msg.group_id
            )
            if reply_chat_message:
                chat_message_lst.append(reply_chat_message)
        chat_handler = self.group_contexts[msg.group_id]
        await self.async_chat_handler(
            chat_message_lst=chat_message_lst,
            chat_handler=chat_handler,
            group_id=msg.group_id,
        )
        return True
