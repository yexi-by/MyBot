"""基于工具调用的 AI 智能群聊回复插件。"""

from typing import ClassVar, override

from app.models import At, GroupMessage, NapCatId
from app.plugins.base import BasePlugin
from app.services import ContextHandler

from .config import AIGroupChatConfig, build_system_prompt, load_ai_group_chat_config
from .constants import (
    CONSUMERS_COUNT,
    DEEPSEEK_V4_ROLEPLAY_MODELS,
    PRIORITY,
)
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
        self.message_builder: GroupChatMessageBuilder = GroupChatMessageBuilder(
            config=self.config,
            database=self.context.database,
            http_client=self.context.direct_httpx,
        )
        self.tool_loop: GroupChatToolLoop = GroupChatToolLoop(
            config=self.config,
            context=self.context,
        )
        for group_config in self.config.group_config:
            system_prompt = build_system_prompt(group_config=group_config)
            self.group_contexts[self._id_key(group_config.group_id)] = ContextHandler(
                system_prompt=system_prompt,
                max_context_length=group_config.max_context_length,
            )

    @override
    async def run(self, msg: GroupMessage) -> bool:
        """在机器人被艾特时触发 AI 群聊回复。"""
        group_key = self._id_key(msg.group_id)
        if group_key not in self.group_contexts:
            return False
        if not self._is_bot_mentioned(msg=msg):
            return False
        chat_handler = self.group_contexts[group_key]
        append_roleplay_instruct = self._should_append_deepseek_v4_roleplay_instruct(
            group_key=group_key,
            chat_handler=chat_handler,
        )
        turn_messages = await self.message_builder.build_turn_messages(
            msg=msg,
            append_deepseek_v4_roleplay_instruct=append_roleplay_instruct,
        )
        if append_roleplay_instruct:
            self.deepseek_v4_roleplay_instruct_groups.add(group_key)
        await self.tool_loop.run(
            msg=msg,
            chat_handler=chat_handler,
            turn_messages=turn_messages,
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
