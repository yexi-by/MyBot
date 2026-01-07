from firecrawl import AsyncFirecrawlApp
from pydantic import TypeAdapter

from app.models import GroupMessage, MessageSegment
from app.services import ContextHandler
from app.services.llm.schemas import ChatMessage
from app.utils import logger

from ..base import BasePlugin
from .segments import AIResponse, MessageContent, Firecrawl
from .utils import (
    build_group_chat_contexts,
    build_message_components,
    convert_basemodel_to_schema,
    get_firecrawl_response,
    extract_at_mentions,
    extract_message_images,
    load_config,
)

# 配置文件路径
GROUP_CONFIG_PATH = "other/group_config.toml"

# 最大重试次数常量
MAX_RETRY_ATTEMPTS = 10


class AIResponsePlugin(BasePlugin[GroupMessage]):
    name = "ai回复插件"
    consumers_count = 5
    priority = 5

    def setup(self) -> None:
        config = load_config(file_path=GROUP_CONFIG_PATH)
        schema = convert_basemodel_to_schema(AIResponse)
        self.group_contexts = build_group_chat_contexts(config=config, schema=schema)
        self.firecrawl_client = AsyncFirecrawlApp(
            api_key=config.firecrawl_config.api_key,
            api_url=config.firecrawl_config.api_url,
        )

    async def _send_reply_message(
        self,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        message_content: MessageContent,
        user_message: ChatMessage,
    ) -> None:
        """发送回复消息并更新聊天上下文"""
        message_segments = build_message_components(send_message=message_content)
        await self.context.bot.send_msg(
            group_id=msg.group_id, message_segment=message_segments
        )
        adapter = TypeAdapter(list[MessageSegment])
        serialized_segments = adapter.dump_json(message_segments).decode("utf-8")
        chat_handler.build_chatmessage(message=user_message)
        chat_handler.build_chatmessage(role="assistant", text=serialized_segments)

    async def _execute_firecrawl_tool(
        self, firecrawl_request: Firecrawl, conversation_history: list[ChatMessage]
    ) -> None:
        """执行 Firecrawl 网页抓取工具并将结果添加到对话历史"""
        tool_response = await get_firecrawl_response(
            firecrawl_request, self.firecrawl_client
        )
        logger.debug(tool_response)
        tool_result_text = f"命令执行结果:\n{tool_response}"
        tool_output_message = ChatMessage(
            role="user", text=f"工具输出:\n{tool_result_text}"
        )
        conversation_history.append(tool_output_message)

    async def _process_ai_conversation(
        self, msg: GroupMessage, chat_handler: ContextHandler
    ) -> bool:
        """处理与 AI 的多轮对话"""
        user_message = await extract_message_images(
            msg=msg, client=self.context.direct_httpx
        )
        conversation_history = chat_handler.messages_lst
        conversation_history.append(user_message)

        attempt_count = 0
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
                    await self._send_reply_message(
                        msg=msg,
                        message_content=message_to_send,
                        chat_handler=chat_handler,
                        user_message=user_message,
                    )

                if firecrawl_request:
                    await self._execute_firecrawl_tool(
                        firecrawl_request=firecrawl_request,
                        conversation_history=conversation_history,
                    )

                if ai_response.end:
                    break

            except Exception as e:
                error_message = ChatMessage(role="user", text=f"错误:\n{e}")
                conversation_history.append(error_message)
        return True

    async def run(self, msg: GroupMessage) -> bool:
        if msg.group_id not in self.group_contexts:
            return False
        mentioned_users = extract_at_mentions(msg=msg)
        if not mentioned_users or self.context.bot.boot_id not in mentioned_users:
            return False
        chat_handler = self.group_contexts[msg.group_id]
        await self._process_ai_conversation(msg=msg, chat_handler=chat_handler)
        return True
