"""基于工具调用的 AI 智能群聊回复插件。"""

from dataclasses import dataclass
from typing import ClassVar, Mapping, override

from app.config import MaterializedAIGroupChatConfig, MaterializedAIGroupConfig
from app.models import At, GroupMessage, NapCatId
from app.plugins.base import BasePlugin
from app.services import ConversationContextKey, ContextHandler
from app.utils.log import log_event

from .constants import CONSUMERS_COUNT, PRIORITY
from .debug_dump import AIGroupChatDebugDumper
from .message_builder import GroupChatMessageBuilder
from .tool_loop import GroupChatToolLoop, TurnContextState
from .vision_tool import VisionDescriptionTool, VisionTurnState


@dataclass(frozen=True, slots=True)
class _AIGroupChatRuntime:
    """单次配置版本对应的 AI 群聊运行对象。"""

    revision: int
    config: MaterializedAIGroupChatConfig
    groups: Mapping[str, MaterializedAIGroupConfig]
    debug_dumper: AIGroupChatDebugDumper
    message_builder: GroupChatMessageBuilder
    vision_tool: VisionDescriptionTool
    tool_loop: GroupChatToolLoop


class AIGroupChatPlugin(BasePlugin[GroupMessage]):
    """处理群聊中的 AI 角色扮演回复。"""

    name: ClassVar[str] = "AI智能群聊回复插件"
    plugin_id: ClassVar[str] = "ai_group_chat"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """初始化配置缓存和调试转储版本。"""
        self._runtime_revision = 0
        self._runtime: _AIGroupChatRuntime | None = None
        self._debug_initialized_revision: dict[ConversationContextKey, int] = {}

    def _current_runtime(self) -> _AIGroupChatRuntime | None:
        """为当前插件配置版本构造一次完整运行对象。"""
        revision = self.plugin_config.revision
        if self._runtime_revision == revision:
            self._synchronize_contexts(runtime=self._runtime)
            return self._runtime
        materialized = self.plugin_config.get(MaterializedAIGroupChatConfig)
        if materialized is None:
            runtime = None
        else:
            config = materialized.source
            debug_dumper = AIGroupChatDebugDumper(config=config)
            message_builder = GroupChatMessageBuilder(
                config=config,
                group_messages=self.context.group_messages,
                bot=self.context.bot,
                http_client=self.context.direct_httpx,
            )
            vision_tool = VisionDescriptionTool(
                config=materialized,
                context=self.context,
            )
            tool_loop = GroupChatToolLoop(
                config=config,
                context=self.context,
                vision_tool=vision_tool,
            )
            runtime = _AIGroupChatRuntime(
                revision=revision,
                config=materialized,
                groups={str(group.source.id): group for group in materialized.groups},
                debug_dumper=debug_dumper,
                message_builder=message_builder,
                vision_tool=vision_tool,
                tool_loop=tool_loop,
            )
            log_event(
                level="DEBUG",
                event="ai_group_chat.config.loaded",
                category="plugin",
                message="AI 群聊插件运行配置已更新",
                revision=revision,
                model_name=config.model.name,
                provider=config.model.provider,
                supports_images=config.model.supports_images,
                debug_dump_messages=config.debug_dump_messages,
                group_count=len(runtime.groups),
            )
        self._runtime = runtime
        self._runtime_revision = revision
        self._synchronize_contexts(runtime=runtime)
        return runtime

    def _synchronize_contexts(self, *, runtime: _AIGroupChatRuntime | None) -> None:
        """在群请求结束后删除停用或提示词已变化的上下文。"""
        store = self.context.conversation_contexts
        for key, handler in store.items_for_owner(owner=self.plugin_id):
            lock = store.lock_for(key=key)
            if lock.locked():
                continue
            group = (
                runtime.groups.get(key.conversation_id)
                if runtime is not None
                else None
            )
            prompt_changed = (
                group is not None
                and handler.system_prompt.text != group.system_prompt
            )
            if group is not None and not prompt_changed:
                continue
            store.remove(key=key)
            self._debug_initialized_revision.pop(key, None)
            if group is None:
                continue
            log_event(
                level="INFO",
                event="ai_group_chat.group_context.reset",
                category="plugin",
                message="AI 群聊提示词或知识库变化，已清空本群内存上下文",
                bot_id=key.bot_id,
                group_id=key.conversation_id,
                max_context_tokens=group.source.max_context_tokens,
                system_prompt_chars=len(group.system_prompt),
            )

    def _get_group_context(
        self,
        *,
        runtime: _AIGroupChatRuntime,
        group: MaterializedAIGroupConfig,
        key: ConversationContextKey,
    ) -> ContextHandler:
        """在群锁内初始化、重置或更新上下文预算。"""
        store = self.context.conversation_contexts
        handler = store.get(key=key)
        reset = (
            handler is not None
            and handler.system_prompt.text != group.system_prompt
        )
        if handler is None or reset:
            handler = ContextHandler(
                system_prompt=group.system_prompt,
                max_context_tokens=group.source.max_context_tokens,
            )
            store.set(key=key, context=handler)
            log_event(
                level="INFO" if reset else "DEBUG",
                event=(
                    "ai_group_chat.group_context.reset"
                    if reset
                    else "ai_group_chat.group_context.initialized"
                ),
                category="plugin",
                message=(
                    "AI 群聊提示词或知识库变化，已清空本群内存上下文"
                    if reset
                    else "AI 群聊上下文初始化完成"
                ),
                bot_id=key.bot_id,
                group_id=key.conversation_id,
                max_context_tokens=group.source.max_context_tokens,
                system_prompt_chars=len(group.system_prompt),
            )
        else:
            handler.max_context_tokens = group.source.max_context_tokens

        if self._debug_initialized_revision.get(key) != runtime.revision:
            dump_path = runtime.debug_dumper.initialize_group(
                group_config=group.source,
                messages=handler.messages_lst,
            )
            self._debug_initialized_revision[key] = runtime.revision
            if dump_path is not None:
                log_event(
                    level="DEBUG",
                    event="ai_group_chat.debug_dump.initialized",
                    category="plugin",
                    message="AI 群聊调试转储已按新配置初始化",
                    bot_id=key.bot_id,
                    group_id=key.conversation_id,
                    debug_dump_path=str(dump_path),
                )
        return handler

    async def _commit_turn_context(
        self,
        *,
        msg: GroupMessage,
        context_key: ConversationContextKey,
        persistent_context: ContextHandler,
        base_revision: int,
        temporary_context: ContextHandler,
        state: TurnContextState,
    ) -> int | None:
        """短暂持锁，把已经完成的临时上下文按完成顺序提交。"""
        if state.persisted_prefix_count is None or not state.commit_requested:
            log_event(
                level="DEBUG",
                event="ai_group_chat.context.commit_skipped",
                category="plugin",
                message="AI 群聊本轮未形成可提交结果，已丢弃临时上下文",
                group_id=msg.group_id,
                message_id=msg.message_id,
                prepared=state.persisted_prefix_count is not None,
                commit_requested=state.commit_requested,
            )
            return None

        store = self.context.conversation_contexts
        lock = store.lock_for(key=context_key)
        async with lock:
            latest_runtime = self._current_runtime()
            latest_group = (
                latest_runtime.groups.get(context_key.conversation_id)
                if latest_runtime is not None
                else None
            )
            current_context = store.get(key=context_key)
            prompt_changed = (
                latest_group is not None
                and persistent_context.system_prompt.text
                != latest_group.system_prompt
            )
            if (
                latest_runtime is None
                or latest_group is None
                or current_context is not persistent_context
                or prompt_changed
            ):
                if current_context is persistent_context and (
                    latest_group is None or prompt_changed
                ):
                    store.remove(key=context_key)
                    self._debug_initialized_revision.pop(context_key, None)
                log_event(
                    level="INFO",
                    event="ai_group_chat.context.commit_skipped",
                    category="plugin",
                    message="AI 群聊配置或上下文已变化，旧请求未写入新长期上下文",
                    bot_id=context_key.bot_id,
                    group_id=context_key.conversation_id,
                    message_id=msg.message_id,
                    context_replaced=current_context is not persistent_context,
                    group_enabled=latest_group is not None,
                    prompt_changed=prompt_changed,
                )
                return None

            current_context = self._get_group_context(
                runtime=latest_runtime,
                group=latest_group,
                key=context_key,
            )
            temporary_messages = temporary_context.messages_lst
            persisted_prefix_count = state.persisted_prefix_count
            if persisted_prefix_count > len(temporary_messages):
                raise RuntimeError("临时上下文提交边界超出消息数量")
            tail_messages = temporary_messages[persisted_prefix_count:]
            can_replace_history = (
                state.replace_existing_history
                and current_context.revision == base_revision
            )
            stripped_history_image_count = current_context.remove_history_images()
            if can_replace_history:
                current_context.replace_history(messages=temporary_messages[1:])
                commit_mode = "replace_compressed_history"
            else:
                current_context.build_chatmessage(
                    message_lst=[*state.fallback_turn_messages, *tail_messages]
                )
                commit_mode = (
                    "append_after_stale_compression"
                    if state.replace_existing_history
                    else "append_completed_turn"
                )
            committed_messages = current_context.messages_lst
            log_event(
                level="DEBUG",
                event="ai_group_chat.context.persist",
                category="plugin",
                message="AI 群聊本轮完成结果已写入长期上下文",
                bot_id=context_key.bot_id,
                group_id=context_key.conversation_id,
                message_id=msg.message_id,
                title=state.title,
                commit_mode=commit_mode,
                turn_messages_count=state.turn_messages_count,
                sent_content_messages_count=state.sent_content_messages_count,
                tool_history_messages_count=state.tool_history_messages_count,
                tool_summary_messages_count=state.tool_summary_messages_count,
                vision_history_messages_count=state.vision_history_messages_count,
                retain_tool_results=latest_runtime.config.source.retain_tool_results,
                replace_existing_history=can_replace_history,
                stripped_history_image_count=stripped_history_image_count,
                current_messages_count=len(committed_messages),
            )
            await latest_runtime.debug_dumper.append_context_snapshot(
                group_id=msg.group_id,
                title=state.title,
                messages=committed_messages,
            )
            return len(committed_messages)

    @override
    async def run(self, msg: GroupMessage) -> bool:
        """在机器人被艾特时触发 AI 群聊回复。"""
        group_key = str(msg.group_id)
        context_key = ConversationContextKey(
            owner=self.plugin_id,
            bot_id=str(msg.self_id),
            conversation_id=group_key,
        )
        runtime = self._current_runtime()
        if runtime is None or group_key not in runtime.groups:
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

        store = self.context.conversation_contexts
        lock = store.lock_for(key=context_key)
        async with lock:
            runtime = self._current_runtime()
            if runtime is None:
                store.remove(key=context_key)
                return False
            group = runtime.groups.get(group_key)
            if group is None:
                store.remove(key=context_key)
                return False
            persistent_context = self._get_group_context(
                runtime=runtime,
                group=group,
                key=context_key,
            )
            base_revision = persistent_context.revision
            temporary_context = persistent_context.fork()
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
                context_messages_count=len(persistent_context.messages_lst),
                config_revision=runtime.revision,
            )

        built_turn_messages = await runtime.message_builder.build_turn_messages(
            msg=msg,
        )
        vision_turn_state = VisionTurnState()
        input_vision_delivery = await runtime.vision_tool.deliver(
            items=built_turn_messages.image_items,
            truncated_count=built_turn_messages.truncated_image_count,
            question=built_turn_messages.question,
            source_name="当前消息和引用消息",
            turn_state=vision_turn_state,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.turn_messages.built",
            category="plugin",
            message="AI 群聊本轮输入构造完成",
            group_id=group_key,
            message_id=msg.message_id,
            turn_messages_count=len(built_turn_messages.turn_messages),
            text_chars=sum(
                len(message.text or "")
                for message in built_turn_messages.turn_messages
            ),
            detected_image_count=built_turn_messages.detected_image_count,
            image_count=built_turn_messages.loaded_image_count,
            image_errors_count=len(built_turn_messages.image_errors),
            truncated_image_count=built_turn_messages.truncated_image_count,
            vision_messages_count=len(input_vision_delivery.working_messages),
            vision_ok=(
                input_vision_delivery.result.ok
                if input_vision_delivery.result is not None
                else None
            ),
            vision_is_error=(
                input_vision_delivery.result.is_error
                if input_vision_delivery.result is not None
                else None
            ),
            vision_observed_count=(
                input_vision_delivery.result.observed_count
                if input_vision_delivery.result is not None
                else 0
            ),
            model_name=runtime.config.source.model.name,
            provider=runtime.config.source.model.provider,
        )
        turn_context_state = TurnContextState()
        committed_messages_count: int | None = None
        try:
            await runtime.tool_loop.run(
                msg=msg,
                chat_handler=temporary_context,
                turn_messages=built_turn_messages.turn_messages,
                input_vision_messages=input_vision_delivery.working_messages,
                input_vision_history_messages=input_vision_delivery.history_messages,
                question=built_turn_messages.question,
                vision_turn_state=vision_turn_state,
                turn_context_state=turn_context_state,
            )
        finally:
            committed_messages_count = await self._commit_turn_context(
                msg=msg,
                context_key=context_key,
                persistent_context=persistent_context,
                base_revision=base_revision,
                temporary_context=temporary_context,
                state=turn_context_state,
            )
        log_event(
            level="DEBUG",
            event="ai_group_chat.event.finished",
            category="plugin",
            message="AI 群聊事件处理完成",
            group_id=group_key,
            message_id=msg.message_id,
            context_committed=committed_messages_count is not None,
            context_messages_count=committed_messages_count,
        )
        return True

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
