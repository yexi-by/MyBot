"""群聊 AI 回复插件。"""

import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from firecrawl import AsyncFirecrawlApp
from pydantic import ValidationError

from app.models import GroupMessage
from app.services import ContextHandler
from app.services.llm.schemas import ChatMessage
from app.utils import (
    bytes_to_text,
    detect_extension,
    download_content,
    get_reply_image_paths,
    load_config_section,
    logger,
    parse_message_chain,
    parse_validated_json,
    pydantic_to_json_schema,
    read_files_content,
)
from app.utils.message_utils import get_reply_message_from_db

from ..base import BasePlugin
from .message_model import GroupFile
from .segments import AIResponse, PluginConfig
from .utils import (
    build_group_chat_contexts,
    build_message_components,
    get_firecrawl_response,
)

# 配置文件路径
PLUGINS_CONFIG_PATH = "plugins_config/plugins.toml"
CONFIG_SECTION = "ai_repose"
# 最大重试次数常量
MAX_RETRY_ATTEMPTS = 20
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

# 插件配置
CONSUMERS_COUNT = 5
PRIORITY = 5
JSON_INDENT = 2


class AIResponsePlugin(BasePlugin[GroupMessage]):
    """处理群聊中的 AI 对话请求。"""

    name = "ai回复插件"
    consumers_count = CONSUMERS_COUNT
    priority = PRIORITY

    def setup(self) -> None:
        """加载插件配置并初始化上下文与工具客户端。"""
        config = load_config_section(
            file_path=PLUGINS_CONFIG_PATH,
            section_name=CONFIG_SECTION,
            model_cls=PluginConfig,
        )
        self.model_name = config.model_name
        self.model_vendors = config.model_vendors
        schema = pydantic_to_json_schema(AIResponse)
        self.group_contexts = build_group_chat_contexts(config=config, schema=schema)
        self.firecrawl_client = AsyncFirecrawlApp(
            api_key=config.firecrawl_config.api_key,
            api_url=config.firecrawl_config.api_url,
        )

    def get_current_time(self) -> str:
        """返回当前北京时间字符串。"""
        beijing_time = datetime.now(ZoneInfo("Asia/Shanghai"))
        time_str = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        return time_str

    async def assemble_user_message(
        self, msg: GroupMessage, image_url_lst: list[str]
    ) -> ChatMessage:
        """将用户消息和附带图片整理为 LLM 输入消息。"""
        image_bytes_lst: list[bytes] = []
        for url in image_url_lst:
            image_bytes = await download_content(
                url=url, client=self.context.direct_httpx
            )
            image_bytes_lst.append(image_bytes)
        text_str = msg.model_dump_json(indent=JSON_INDENT)
        text = f"以下是用户发言:\n{text_str}"
        chat_message = ChatMessage(
            role="user", image=image_bytes_lst if image_bytes_lst else None, text=text
        )
        return chat_message

    async def get_group_file(
        self,
        group_file: GroupFile,
        group_id: int,
    ) -> str:
        """根据结构化指令读取群文件信息或下载具体文件内容。"""
        root_file = group_file.group_root_file
        files_by_folder = group_file.group_files_by_folder
        file = group_file.group_file
        if root_file:
            response = await self.context.bot.get_group_root_files(
                group_id=group_id, file_count=root_file.file_count
            )
            return response.model_dump_json()
        if files_by_folder:
            folder = files_by_folder.folder
            if folder.startswith("/"):
                folder = folder[1:]
            response = await self.context.bot.get_group_files_by_folder(
                group_id=group_id,
                folder=folder,
                file_count=files_by_folder.file_count,
            )
            return response.model_dump_json()

        if file:
            response = await self.context.bot.get_group_file_url(
                group_id=group_id, file_id=file.file_id
            )
            url = response.data.get("url", None)
            if not url:
                raise ValueError(f"url是空的,详情:{response}")
            content = await download_content(url=url, client=self.context.direct_httpx)
            file_extension = detect_extension(data=content)
            text = await bytes_to_text(
                file_bytes=content, file_extension=file_extension
            )
            return text
        raise ValueError("GroupFile为空")

    async def assemble_reply_message_details(
        self, reply_id: int, group_id: int
    ) -> ChatMessage | None:
        """将被回复消息补充到当前对话上下文。"""
        image_bytes_list = None
        self_id = self.context.bot.boot_id
        reply_message = await get_reply_message_from_db(
            database=self.context.database,
            self_id=self_id,
            group_id=group_id,
            reply_id=reply_id,
        )
        if not reply_message:
            return None
        image_path = get_reply_image_paths(reply_message=reply_message)
        if image_path:
            image_bytes_list = read_files_content(
                file_paths=image_path, output_type="bytes"
            )
        text_str = reply_message.model_dump_json(indent=JSON_INDENT)
        text = (
            f"### 上下文补充: 用户回复的消息内容\n"
            f"用户是对以下消息进行的回复（请基于此理解用户的意图）:\n"
            f"```json\n{text_str}\n```"
        )
        chat_message = ChatMessage(role="user", image=image_bytes_list, text=text)
        return chat_message

    async def async_chat_handler(
        self,
        chat_message_lst: list[ChatMessage],
        chat_handler: ContextHandler,
        group_id: int,
    ) -> None:
        """驱动 LLM 对话循环，并处理工具调用与最终回复。"""
        conversation_history = (
            chat_handler.messages_lst
        )  # 当前轮次的历史对话 这里拿到得只是浅拷贝 不会影响实例属性
        conversation_history.extend(chat_message_lst)
        attempt_count = 0
        # 最终存入数据库的历史对话,剔除了token爆炸的工具输出 浅拷贝,防止后面那天忘记了
        history_chat_list: list[ChatMessage] = chat_message_lst[:]
        while attempt_count <= MAX_RETRY_ATTEMPTS:
            attempt_count += 1
            raw_response = await self.context.llm.get_ai_text_response(
                messages=conversation_history,
                model_name=self.model_name,
                model_vendors=self.model_vendors,
            )
            logger.debug(raw_response)
            try:
                ai_response = parse_validated_json(raw_response, AIResponse)
                assistant_message = ChatMessage(role="assistant", text=raw_response)
                history_chat_list.append(assistant_message)
                conversation_history.append(assistant_message)
                message_to_send = ai_response.send_message
                firecrawl_request = ai_response.firecrawl
                group_file = ai_response.group_file
                if message_to_send:
                    message_segments = build_message_components(
                        send_message=message_to_send
                    )
                    await self.context.bot.send_msg(
                        group_id=group_id, message_segment=message_segments
                    )
                if firecrawl_request:
                    tool_response = await get_firecrawl_response(
                        firecrawl=firecrawl_request, client=self.firecrawl_client
                    )
                    logger.debug(tool_response)
                    tool_output_message = ChatMessage(
                        role="user",
                        text=(
                            f"### 工具 (Firecrawl) 执行结果:\n"
                            f"```text\n{tool_response}\n```\n"
                            f"请根据以上搜索结果继续回答。"
                        ),
                    )
                    conversation_history.append(tool_output_message)
                if group_file:
                    response = await self.get_group_file(
                        group_file=group_file, group_id=group_id
                    )
                    logger.debug(response)
                    group_file_message = ChatMessage(
                        role="user",
                        text=(
                            f"### 工具 (GroupFile) 执行结果:\n"
                            f"```text\n{response}\n```\n"
                            f"请根据以上文件内容继续回答。"
                        ),
                    )
                    conversation_history.append(group_file_message)

                if ai_response.end is True:
                    chat_handler.build_chatmessage(message_lst=history_chat_list)
                    break
                if not firecrawl_request and not group_file:
                    # 防止模型理解能有问题出bug 导致以assistant结尾
                    chat_handler.build_chatmessage(message_lst=history_chat_list)
                    break
            except ValidationError as ve:
                logger.warning(f"LLM JSON 格式错误: {ve}")
                error_text = (
                    f"系统提示: 你生成的 JSON 格式无法解析，请严格按照 Schema 定义修正你的输出。\n"
                    f"错误详情:\n{ve}\n"
                    f"请重新生成 JSON。"
                )
                error_message = ChatMessage(role="user", text=error_text)
                conversation_history.append(error_message)

            except Exception:
                full_error = traceback.format_exc()
                logger.exception("处理过程中发生内部错误")
                error_message = ChatMessage(
                    role="user",
                    text=f"命令执行出错了，请根据以下堆栈信息修正代码:\n```python\n{full_error}\n```",
                )
                conversation_history.append(error_message)

    async def run(self, msg: GroupMessage) -> bool:
        """处理群消息事件并在需要时触发 AI 回复。"""
        if msg.group_id not in self.group_contexts:
            return False
        at_lst, text_list, image_url_lst, reply_id = parse_message_chain(msg=msg)
        text = "".join(text_list).strip()
        if text == HELP_TOKEN:
            await self.context.bot.send_msg(
                group_id=msg.group_id, at=msg.user_id, text=HELP_TEXT
            )
            return True

        if self.context.bot.boot_id not in at_lst:
            return False
        chat_message_lst: list[ChatMessage] = []
        time_str = self.get_current_time()
        time_text = f"当前时间:{time_str}\n"
        time_message = ChatMessage(role="user", text=time_text)
        chat_message_lst.append(time_message)
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
