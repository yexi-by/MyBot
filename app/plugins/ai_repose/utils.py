from firecrawl import AsyncFirecrawlApp
from app.models import At, MessageSegment, Reply, Text
from app.services import ContextHandler
from app.utils import (
    load_text_file_sync,
)

from .firecrawl_model import Firecrawl
from .segments import MessageContent, PluginConfig


def build_group_chat_contexts(
    config: PluginConfig, schema: str
) -> dict[int, ContextHandler]:
    """根据配置构建群聊上下文处理器字典"""
    group_contexts: dict[int, ContextHandler] = {}
    for group_settings in config.group_config:
        system_prompt = load_text_file_sync(file_path=group_settings.system_prompt_path)
        knowledge_base = load_text_file_sync(
            file_path=group_settings.knowledge_base_path
        )
        function_path = load_text_file_sync(file_path=group_settings.function_path)
        combined_prompt = "\n\n".join(
            [system_prompt, knowledge_base, function_path, schema]
        )
        group_contexts[group_settings.group_id] = ContextHandler(
            system_prompt=combined_prompt,
            max_context_length=group_settings.max_context_length,
        )
    return group_contexts


def build_message_components(send_message: MessageContent) -> list[MessageSegment]:
    """构建消息段列表"""
    message_segments: list[MessageSegment] = []
    text = send_message.text
    at = send_message.at
    reply = send_message.reply_to_message_id

    if at:
        at_segment = At.new(qq=at)
        message_segments.append(at_segment)
    if text:
        text_segment = Text.new(text=text)
        message_segments.append(text_segment)

    if reply:
        reply_segment = Reply.new(id=reply)
        message_segments.append(reply_segment)
    return message_segments


async def get_firecrawl_response(
    firecrawl: Firecrawl, client: AsyncFirecrawlApp
) -> str:
    result: list[str] = []
    if firecrawl.scrape:
        response = await client.scrape(**firecrawl.scrape.model_dump())
        result.append(response.model_dump_json(exclude_none=True, indent=2))
    if firecrawl.search:
        response = await client.search(**firecrawl.search.model_dump())
        result.append(response.model_dump_json(exclude_none=True, indent=2))
    if firecrawl.map:
        response = await client.map(**firecrawl.map.model_dump())
        result.append(response.model_dump_json(exclude_none=True, indent=2))
    result_str = "\n".join(result)
    return result_str
