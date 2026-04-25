"""基于工具调用的 AI 智能群聊回复插件。"""

from typing import ClassVar, override

from app.models import At, GroupMessage, NapCatId
from app.plugins.base import BasePlugin
from app.services import ContextHandler
from app.utils.log import log_event

from .config import AIGroupChatConfig, build_system_prompt, load_ai_group_chat_config
from .constants import (
    CONSUMERS_COUNT,
    DEEPSEEK_V4_ROLEPLAY_MODELS,
    PRIORITY,
)
from .debug_dump import AIGroupChatDebugDumper
from .message_builder import GroupChatMessageBuilder
from .tool_loop import GroupChatToolLoop


class AIGroupChatPlugin(BasePlugin[GroupMessage]):
    """处理群聊中的 AI 角色扮演回复。"""

    name: ClassVar[str] = "AI智能群聊回复插件"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """读取插件配置并初始化每个群的上下文。"""
        self.config: AIGroupChatConfig = load_ai_group_chat_config()
        self.group_contexts: dict[str, ContextHandler] = {}
        self.deepseek_v4_roleplay_instruct_groups: set[str] = set()
        self.debug_dumper: AIGroupChatDebugDumper = AIGroupChatDebugDumper(
            config=self.config
        )
        self.message_builder: GroupChatMessageBuilder = GroupChatMessageBuilder(
            config=self.config,
            database=self.context.database,
            http_client=self.context.direct_httpx,
        )
        self.tool_loop: GroupChatToolLoop = GroupChatToolLoop(
            config=self.config,
            context=self.context,
            debug_dumper=self.debug_dumper,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.setup.start",
            category="plugin",
            message="AI 群聊插件开始初始化",
            model_name=self.config.model_name,
            model_vendors=self.config.model_vendors,
            supports_multimodal=self.config.supports_multimodal,
            debug_dump_messages=self.config.debug_dump_messages,
            group_count=len(self.config.group_config),
        )
        for group_config in self.config.group_config:
            system_prompt = build_system_prompt(group_config=group_config)
            group_key = self._id_key(group_config.group_id)
            chat_handler = ContextHandler(
                system_prompt=system_prompt,
                max_context_length=group_config.max_context_length,
            )
            self.group_contexts[group_key] = chat_handler
            dump_path = self.debug_dumper.initialize_group(
                group_config=group_config,
                messages=chat_handler.messages_lst,
            )
            log_event(
                level="DEBUG",
                event="ai_group_chat.group_context.initialized",
                category="plugin",
                message="AI 群聊上下文初始化完成",
                group_id=group_key,
                max_context_length=group_config.max_context_length,
                system_prompt_chars=len(system_prompt),
                debug_dump_path=str(dump_path) if dump_path is not None else "",
            )

    @override
    async def run(self, msg: GroupMessage) -> bool:
        """在机器人被艾特时触发 AI 群聊回复。"""
        group_key = self._id_key(msg.group_id)
        if group_key not in self.group_contexts:
            log_event(
                level="DEBUG",
                event="ai_group_chat.event.skipped_unconfigured_group",
                category="plugin",
                message="群未配置 AI 群聊插件，已跳过",
                group_id=group_key,
                message_id=msg.message_id,
                user_id=msg.user_id,
            )
            return False
        if not self._is_bot_mentioned(msg=msg):
            log_event(
                level="DEBUG",
                event="ai_group_chat.event.skipped_without_mention",
                category="plugin",
                message="群消息没有艾特机器人，已跳过 AI 回复",
                group_id=group_key,
                message_id=msg.message_id,
                user_id=msg.user_id,
                raw_message=msg.raw_message,
                segment_count=len(msg.message),
            )
            return False
        chat_handler = self.group_contexts[group_key]
        append_roleplay_instruct = self._should_append_deepseek_v4_roleplay_instruct(
            group_key=group_key,
            chat_handler=chat_handler,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.event.accepted",
            category="plugin",
            message="群消息命中 AI 回复条件",
            group_id=group_key,
            message_id=msg.message_id,
            user_id=msg.user_id,
            raw_message=msg.raw_message,
            segment_count=len(msg.message),
            context_messages_count=len(chat_handler.messages_lst),
            append_roleplay_instruct=append_roleplay_instruct,
        )
        turn_messages = await self.message_builder.build_turn_messages(
            msg=msg,
            append_deepseek_v4_roleplay_instruct=append_roleplay_instruct,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.turn_messages.built",
            category="plugin",
            message="AI 群聊本轮输入构造完成",
            group_id=group_key,
            message_id=msg.message_id,
            turn_messages_count=len(turn_messages),
            text_chars=sum(len(message.text or "") for message in turn_messages),
            image_count=sum(len(message.image or []) for message in turn_messages),
        )
        if append_roleplay_instruct:
            self.deepseek_v4_roleplay_instruct_groups.add(group_key)
        await self.tool_loop.run(
            msg=msg,
            chat_handler=chat_handler,
            turn_messages=turn_messages,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.event.finished",
            category="plugin",
            message="AI 群聊事件处理完成",
            group_id=group_key,
            message_id=msg.message_id,
            context_messages_count=len(chat_handler.messages_lst),
        )
        return True

    def _should_append_deepseek_v4_roleplay_instruct(
        self, *, group_key: str, chat_handler: ContextHandler
    ) -> bool:
        """判断是否应在首轮用户输入末尾追加 DeepSeek V4 角色沉浸 Marker。"""
        return (
            self.config.enable_deepseek_v4_roleplay_instruct
            and self.config.model_name in DEEPSEEK_V4_ROLEPLAY_MODELS
            and group_key not in self.deepseek_v4_roleplay_instruct_groups
            and self._is_first_user_turn(chat_handler=chat_handler)
        )

    def _is_first_user_turn(self, *, chat_handler: ContextHandler) -> bool:
        """判断当前群上下文是否还没有用户消息。"""
        return not any(message.role == "user" for message in chat_handler.messages_lst)

    def _is_bot_mentioned(self, *, msg: GroupMessage) -> bool:
        """判断当前群消息是否艾特了机器人。"""
        bot_id = self.context.bot.boot_id if self.context.bot.boot_id != "" else msg.self_id
        return any(mention_id == bot_id for mention_id in self._extract_mentions(msg=msg))

    def _extract_mentions(self, *, msg: GroupMessage) -> list[NapCatId]:
        """提取群消息中的艾特对象。"""
        mentions: list[NapCatId] = []
        for segment in msg.message:
            if isinstance(segment, At) and segment.data.qq != "all":
                mentions.append(segment.data.qq)
        return mentions

    def _id_key(self, value: NapCatId) -> str:
        """把 NapCat ID 作为字典键使用。"""
        return value
