import json
from pathlib import Path

import httpx
from firecrawl import AsyncFirecrawlApp
from pydantic import BaseModel

from app.models import At, BaseMessage, GroupMessage, MessageSegment, Reply, Text
from app.services import ContextHandler
from app.services.llm.schemas import ChatMessage
from app.utils import download_image, load_text_file_sync, load_toml_file, logger

from .firecrawl_model import Firecrawl
from .segments import MessageContent, PluginConfig


def load_config(file_path: str | Path) -> PluginConfig:
    """从TOML文件加载配置并返回PluginConfig对象"""
    path = Path(file_path)
    config_data = load_toml_file(file_path=path)
    config = PluginConfig(**config_data)
    return config


def convert_basemodel_to_schema(model_class: type[BaseModel]) -> str:
    """将BaseModel模型转换为LLM能理解的JSON Schema字符串"""
    schema = json.dumps(model_class.model_json_schema(), indent=2, ensure_ascii=False)
    return schema


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


async def extract_message_images(
    msg: BaseMessage, client: httpx.AsyncClient
) -> ChatMessage:
    """从消息中提取所有图片并构建ChatMessage对象"""
    image_bytes_list: list[bytes] = []
    for segment in msg.message:
        if segment.type == "image":
            url = segment.data.url
            if url is None:
                logger.warning("缺少url")
                continue
            image_bytes = await download_image(url=url, client=client)
            image_bytes_list.append(image_bytes)
    text = msg.model_dump_json()
    chat_message = ChatMessage(role="user", image=image_bytes_list, text=text)
    return chat_message


def extract_at_mentions(msg: GroupMessage) -> list[int | str]:
    """从群消息中提取所有@提及的用户QQ号"""
    at_list: list[int | str] = []
    for segment in msg.message:
        if segment.type != "at":
            continue
        at_list.append(segment.data.qq)
    return at_list


def build_message_components(send_message: MessageContent) -> list[MessageSegment]:
    """构建消息段列表"""
    message_segments: list[MessageSegment] = []
    text = send_message.text
    at = send_message.at
    reply = send_message.reply_to_message_id
    if text:
        text_segment = Text.new(text=text)
        message_segments.append(text_segment)
    if at:
        at_segment = At.new(qq=at)
        message_segments.append(at_segment)
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
        result.append(response.model_dump_json(exclude_none=True))
    if firecrawl.search:
        response = await client.search(**firecrawl.search.model_dump())
        result.append(response.model_dump_json(exclude_none=True))
    if firecrawl.map:
        response = await client.map(**firecrawl.map.model_dump())
        result.append(response.model_dump_json(exclude_none=True))
    result_str = "\n".join(result)
    return result_str
