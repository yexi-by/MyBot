"""AI 群聊插件的工具调用循环。"""

from dataclasses import dataclass

from app.models import GroupMessage, JsonObject, JsonValue
from app.plugins.base import Context
from app.services import (
    ChatMessage,
    CompositeToolExecutor,
    ContextHandler,
    NapCatGroupToolExecutor,
)
from app.services.llm.schemas import LLMResponse, LLMToolCall, LLMToolDefinition
from app.services.llm.tools import build_tool_result_message
from app.utils.log import log_event

from .config import AIGroupChatConfig
from .constants import DEEPSEEK_V4_ROLEPLAY_MODELS
from .context_compressor import GroupChatContextCompressor
from .debug_dump import AIGroupChatDebugDumper
from .token_budget import ConservativeTokenEstimator, TokenBudgetEstimate


@dataclass(frozen=True)
class ReplyContent:
    """区分群内可见回复和写入长期上下文的回复。"""

    visible_content: str | None
    memory_content: str | None
    memory_reasoning_content: str | None


@dataclass(frozen=True)
class PreparedTurnContext:
    """描述本轮请求前整理好的工作上下文。"""

    working_messages: list[ChatMessage]
    turn_messages: list[ChatMessage]
    replace_existing_history: bool


class GroupChatToolLoop:
    """执行群聊专用的 OpenAI 工具调用流程。"""

    def __init__(
        self,
        *,
        config: AIGroupChatConfig,
        context: Context,
        debug_dumper: AIGroupChatDebugDumper,
    ) -> None:
        """保存工具循环所需的配置和运行上下文。"""
        self.config: AIGroupChatConfig = config
        self.context: Context = context
        self.debug_dumper: AIGroupChatDebugDumper = debug_dumper
        self.token_estimator: ConservativeTokenEstimator = ConservativeTokenEstimator(
            safety_factor=config.token_estimation_safety_factor
        )
        self.context_compressor: GroupChatContextCompressor = (
            GroupChatContextCompressor()
        )

    async def run(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
    ) -> None:
        """执行群聊专用工具调用循环。"""
        tool_history_messages: list[ChatMessage] = []
        sent_content_messages: list[ChatMessage] = []
        napcat_executor = NapCatGroupToolExecutor(
            bot=self.context.bot,
            database=self.context.database,
            event=msg,
            allow_mention_all=self.config.allow_mention_all,
        )
        tool_executor = CompositeToolExecutor(
            [napcat_executor, self.context.mcp_tool_manager]
        )
        tools = tool_executor.list_tools()
        prepared_context = await self._prepare_turn_context(
            msg=msg,
            chat_handler=chat_handler,
            turn_messages=turn_messages,
            tools=tools,
        )
        working_messages = prepared_context.working_messages
        turn_messages = prepared_context.turn_messages
        replace_existing_history = prepared_context.replace_existing_history
        log_event(
            level="DEBUG",
            event="ai_group_chat.tool_loop.start",
            category="plugin",
            message="AI 群聊工具循环开始",
            group_id=msg.group_id,
            message_id=msg.message_id,
            model_name=self.config.model_name,
            model_vendors=self.config.model_vendors,
            working_messages_count=len(working_messages),
            tools_count=len(tools),
            tool_names=[tool.name for tool in tools],
        )
        for round_index in range(1, self.config.max_tool_rounds + 1):
            log_event(
                level="DEBUG",
                event="ai_group_chat.llm.request",
                category="plugin",
                message="准备请求 LLM",
                group_id=msg.group_id,
                message_id=msg.message_id,
                round_index=round_index,
                messages_count=len(working_messages),
                tools_count=len(tools),
                tool_choice="auto",
            )
            await self.debug_dumper.append_llm_request(
                group_id=msg.group_id,
                round_index=round_index,
                messages=working_messages,
                tools=tools,
                tool_choice="auto",
            )
            response = await self.context.llm.get_ai_response_with_tools(
                messages=working_messages,
                model_vendors=self.config.model_vendors,
                model_name=self.config.model_name,
                tools=tools,
            )
            content = self._normalize_content(response.content)
            reply_content = self._build_reply_content(
                content=content,
                reasoning_content=response.reasoning_content,
            )
            modifier_calls, information_calls = self._split_tool_calls(
                tool_calls=response.tool_calls
            )
            log_event(
                level="DEBUG",
                event="ai_group_chat.llm.response",
                category="plugin",
                message="LLM 返回群聊响应",
                group_id=msg.group_id,
                message_id=msg.message_id,
                round_index=round_index,
                content_chars=len(content or ""),
                visible_content_chars=len(reply_content.visible_content or ""),
                memory_content_chars=len(reply_content.memory_content or ""),
                reasoning_chars=len(response.reasoning_content or ""),
                tool_calls_count=len(response.tool_calls),
                modifier_tool_calls_count=len(modifier_calls),
                information_tool_calls_count=len(information_calls),
                tool_call_names=[tool_call.name for tool_call in response.tool_calls],
            )
            await self.debug_dumper.append_llm_response(
                group_id=msg.group_id,
                round_index=round_index,
                response=response,
                modifier_calls=modifier_calls,
                information_calls=information_calls,
            )
            if not response.tool_calls:
                await self._finish_plain_response(
                    msg=msg,
                    chat_handler=chat_handler,
                    turn_messages=turn_messages,
                    sent_content_messages=sent_content_messages,
                    tool_history_messages=tool_history_messages,
                    visible_content=reply_content.visible_content,
                    memory_content=reply_content.memory_content,
                    memory_reasoning_content=reply_content.memory_reasoning_content,
                    replace_existing_history=replace_existing_history,
                )
                return
            if modifier_calls and information_calls:
                mixed_history, has_modifier_error = (
                    await self._execute_mixed_tool_calls_with_optional_send(
                        msg=msg,
                        napcat_executor=napcat_executor,
                        working_messages=working_messages,
                        response=response,
                        reply_content=reply_content,
                        modifier_calls=modifier_calls,
                        information_calls=information_calls,
                        tool_executor=tool_executor,
                        sent_content_messages=sent_content_messages,
                    )
                )
                tool_history_messages.extend(mixed_history)
                if has_modifier_error:
                    napcat_executor.clear_message_modifiers()
                continue
            if modifier_calls:
                modifier_history, has_modifier_error = await self._execute_tool_calls(
                    working_messages=working_messages,
                    response=response,
                    tool_calls=modifier_calls,
                    tool_executor=tool_executor,
                    group_id=msg.group_id,
                )
                tool_history_messages.extend(modifier_history)
                if has_modifier_error:
                    napcat_executor.clear_message_modifiers()
                    log_event(
                        level="DEBUG",
                        event="ai_group_chat.modifier_tool.error",
                        category="plugin",
                        message="消息修饰工具执行失败，已清理本地消息修饰状态",
                        group_id=msg.group_id,
                        message_id=msg.message_id,
                        round_index=round_index,
                    )
                    continue
                if reply_content.visible_content is None:
                    log_event(
                        level="WARNING",
                        event="ai_group_chat.modifier_without_content",
                        category="plugin",
                        message="模型调用了 QQ 消息修饰工具但没有输出正文，已转入二次正文生成",
                    )
                    reply_content = await self._request_final_content_after_modifiers(
                        group_id=msg.group_id,
                        round_index=round_index,
                        working_messages=working_messages,
                        tool_history_messages=tool_history_messages,
                        tools=tools,
                    )
                if reply_content.visible_content is None:
                    napcat_executor.clear_message_modifiers()
                    log_event(
                        level="WARNING",
                        event="ai_group_chat.final_content_empty",
                        category="plugin",
                        message="AI 群聊消息修饰工具已执行，但最终正文仍为空，本轮静默结束",
                    )
                    self._persist_turn(
                        chat_handler=chat_handler,
                        turn_messages=turn_messages,
                        sent_content_messages=sent_content_messages,
                        tool_history_messages=tool_history_messages,
                        assistant_content=None,
                        assistant_reasoning_content=None,
                        replace_existing_history=replace_existing_history,
                    )
                    await self.debug_dumper.append_context_snapshot(
                        group_id=msg.group_id,
                        title="本轮静默结束后的长期上下文",
                        messages=chat_handler.messages_lst,
                    )
                    return
                await self._send_reply_content(
                    msg=msg,
                    reply_content=reply_content,
                    napcat_executor=napcat_executor,
                    use_modifiers=True,
                    sent_content_messages=sent_content_messages,
                    event_name="ai_group_chat.reply.sent_with_modifiers",
                    log_message="已发送带消息修饰的 AI 群聊回复",
                )
                log_event(
                    level="DEBUG",
                    event="ai_group_chat.modifier_tool.finished",
                    category="plugin",
                    message="消息修饰工具本轮内容已发送，准备结束本轮",
                    group_id=msg.group_id,
                    message_id=msg.message_id,
                )
                self._persist_turn(
                    chat_handler=chat_handler,
                    turn_messages=turn_messages,
                    sent_content_messages=sent_content_messages,
                    tool_history_messages=tool_history_messages,
                    assistant_content=None,
                    assistant_reasoning_content=None,
                    replace_existing_history=replace_existing_history,
                )
                await self.debug_dumper.append_context_snapshot(
                    group_id=msg.group_id,
                    title="本轮完成后的长期上下文",
                    messages=chat_handler.messages_lst,
                )
                return
            await self._send_reply_content(
                msg=msg,
                reply_content=reply_content,
                napcat_executor=napcat_executor,
                use_modifiers=False,
                sent_content_messages=sent_content_messages,
                event_name="ai_group_chat.reply.sent_with_information_tools",
                log_message="模型返回内容并请求信息工具，已先发送内容后继续执行工具",
            )
            information_history, _ = await self._execute_tool_calls(
                working_messages=working_messages,
                response=response,
                tool_calls=information_calls,
                tool_executor=tool_executor,
                group_id=msg.group_id,
            )
            tool_history_messages.extend(information_history)
        raise RuntimeError(f"AI 群聊工具调用超过最大轮数: {self.config.max_tool_rounds}")

    async def _prepare_turn_context(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        tools: list[LLMToolDefinition],
    ) -> PreparedTurnContext:
        """在请求模型前按 token 预算决定是否压缩旧上下文。"""
        candidate_messages = [*chat_handler.messages_lst, *turn_messages]
        budget = self.token_estimator.check_request(
            messages=candidate_messages,
            tools=tools,
            max_context_tokens=chat_handler.max_context_tokens,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.context_budget.checked",
            category="plugin",
            message="AI 群聊上下文预算检查完成",
            group_id=msg.group_id,
            message_id=msg.message_id,
            estimated_tokens=budget.estimated_tokens,
            max_context_tokens=budget.max_context_tokens,
            should_compress=budget.should_compress,
            current_history_messages_count=len(chat_handler.messages_lst),
            turn_messages_count=len(turn_messages),
            tools_count=len(tools),
        )
        if not budget.should_compress:
            return PreparedTurnContext(
                working_messages=candidate_messages,
                turn_messages=turn_messages,
                replace_existing_history=False,
            )
        _ = await self.context.bot.send_msg(
            group_id=msg.group_id,
            text=self.config.context_compression_notice,
        )
        summary = await self._compress_existing_context(
            msg=msg,
            chat_handler=chat_handler,
            budget=budget,
        )
        rebuilt_user_message = self.context_compressor.build_rebuilt_user_message(
            summary=summary,
            current_turn_messages=turn_messages,
            append_roleplay_instruct=self._should_append_roleplay_instruct_after_compression(),
        )
        rebuilt_turn_messages = [rebuilt_user_message]
        rebuilt_working_messages = [chat_handler.system_prompt, *rebuilt_turn_messages]
        rebuilt_budget = self.token_estimator.check_request(
            messages=rebuilt_working_messages,
            tools=tools,
            max_context_tokens=chat_handler.max_context_tokens,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.context_compressed.rebuilt",
            category="plugin",
            message="AI 群聊上下文压缩后已重建本轮请求",
            group_id=msg.group_id,
            message_id=msg.message_id,
            rebuilt_estimated_tokens=rebuilt_budget.estimated_tokens,
            max_context_tokens=rebuilt_budget.max_context_tokens,
            rebuilt_should_still_compress=rebuilt_budget.should_compress,
            rebuilt_user_chars=len(rebuilt_user_message.text or ""),
            rebuilt_image_count=len(rebuilt_user_message.image or []),
        )
        if rebuilt_budget.should_compress:
            raise RuntimeError(
                "AI 群聊上下文压缩后仍超过最大上下文预算，"
                f"estimated={rebuilt_budget.estimated_tokens}, "
                f"max={rebuilt_budget.max_context_tokens}"
            )
        return PreparedTurnContext(
            working_messages=rebuilt_working_messages,
            turn_messages=rebuilt_turn_messages,
            replace_existing_history=True,
        )

    async def _compress_existing_context(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        budget: TokenBudgetEstimate,
    ) -> str:
        """用临时 LLM 请求压缩已有正式上下文，不包含当前新消息。"""
        history_messages = chat_handler.messages_lst[1:]
        compression_messages, compression_input = (
            self.context_compressor.build_compression_messages(
                system_prompt=chat_handler.system_prompt,
                history_messages=history_messages,
            )
        )
        log_event(
            level="WARNING",
            event="ai_group_chat.context_compression.triggered",
            category="plugin",
            message="AI 群聊上下文超过预算，开始压缩旧上下文",
            group_id=msg.group_id,
            message_id=msg.message_id,
            estimated_tokens=budget.estimated_tokens,
            max_context_tokens=budget.max_context_tokens,
            old_history_messages_count=len(history_messages),
            dropped_image_count=compression_input.dropped_image_count,
        )
        summary = await self.context.llm.get_ai_text_response(
            messages=compression_messages,
            model_vendors=self.config.model_vendors,
            model_name=self.config.model_name,
        )
        normalized_summary = self._normalize_content(summary)
        if normalized_summary is None:
            raise ValueError("AI 群聊上下文压缩返回了空摘要")
        await self.debug_dumper.append_context_compression(
            group_id=msg.group_id,
            estimated_tokens=budget.estimated_tokens,
            max_context_tokens=budget.max_context_tokens,
            old_messages=compression_input.messages,
            dropped_image_count=compression_input.dropped_image_count,
            summary=normalized_summary,
        )
        log_event(
            level="DEBUG",
            event="ai_group_chat.context_compression.finished",
            category="plugin",
            message="AI 群聊旧上下文压缩完成",
            group_id=msg.group_id,
            message_id=msg.message_id,
            summary_chars=len(normalized_summary),
        )
        return normalized_summary

    def _should_append_roleplay_instruct_after_compression(self) -> bool:
        """判断压缩重建后的首条 user 是否需要重新追加角色沉浸要求。"""
        return (
            self.config.enable_deepseek_v4_roleplay_instruct
            and self.config.model_name in DEEPSEEK_V4_ROLEPLAY_MODELS
        )

    def _split_tool_calls(
        self, *, tool_calls: list[LLMToolCall]
    ) -> tuple[list[LLMToolCall], list[LLMToolCall]]:
        """按 NapCat 消息修饰工具和信息工具拆分工具调用。"""
        modifier_calls: list[LLMToolCall] = []
        information_calls: list[LLMToolCall] = []
        for tool_call in tool_calls:
            if NapCatGroupToolExecutor.is_message_modifier_tool(tool_call.name):
                modifier_calls.append(tool_call)
                continue
            information_calls.append(tool_call)
        return modifier_calls, information_calls

    async def _finish_plain_response(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        sent_content_messages: list[ChatMessage],
        tool_history_messages: list[ChatMessage],
        visible_content: str | None,
        memory_content: str | None,
        memory_reasoning_content: str | None,
        replace_existing_history: bool,
    ) -> None:
        """处理没有工具调用的最终响应。"""
        if visible_content is not None:
            _ = await self.context.bot.send_msg(
                group_id=msg.group_id, text=visible_content
            )
            log_event(
                level="DEBUG",
                event="ai_group_chat.reply.sent_plain",
                category="plugin",
                message="已发送普通 AI 群聊回复",
                group_id=msg.group_id,
                message_id=msg.message_id,
                visible_content_chars=len(visible_content),
                memory_content_chars=len(memory_content or ""),
            )
        else:
            log_event(
                level="DEBUG",
                event="ai_group_chat.reply.silent",
                category="plugin",
                message="AI 群聊本轮无工具且无正文，静默结束",
                group_id=msg.group_id,
                message_id=msg.message_id,
            )
        self._persist_turn(
            chat_handler=chat_handler,
            turn_messages=turn_messages,
            sent_content_messages=sent_content_messages,
            tool_history_messages=tool_history_messages,
            assistant_content=memory_content,
            assistant_reasoning_content=memory_reasoning_content,
            replace_existing_history=replace_existing_history,
        )
        await self.debug_dumper.append_context_snapshot(
            group_id=msg.group_id,
            title="本轮完成后的长期上下文",
            messages=chat_handler.messages_lst,
        )

    async def _request_final_content_after_modifiers(
        self,
        *,
        group_id: str,
        round_index: int,
        working_messages: list[ChatMessage],
        tool_history_messages: list[ChatMessage],
        tools: list[LLMToolDefinition],
    ) -> ReplyContent:
        """消息修饰工具缺正文时，禁用工具并二次请求最终群聊正文。"""
        prompt_message = ChatMessage(
            role="user",
            text=(
                "你已经成功调用 QQ 消息修饰工具。"
                "现在请只输出本轮最终群聊正文，不要再调用任何工具；"
                "正文必须是可以直接发送到群里的自然语言。"
            ),
        )
        working_messages.append(prompt_message)
        tool_history_messages.append(prompt_message)
        log_event(
            level="DEBUG",
            event="ai_group_chat.llm.final_content_request",
            category="plugin",
            message="消息修饰工具缺正文，准备禁用工具二次请求正文",
            group_id=group_id,
            round_index=round_index,
            messages_count=len(working_messages),
        )
        await self.debug_dumper.append_llm_request(
            group_id=group_id,
            round_index=round_index,
            messages=working_messages,
            tools=tools,
            tool_choice="none",
        )
        response = await self.context.llm.get_ai_response_with_tools(
            messages=working_messages,
            model_vendors=self.config.model_vendors,
            model_name=self.config.model_name,
            tools=tools,
            tool_choice="none",
        )
        await self.debug_dumper.append_llm_response(
            group_id=group_id,
            round_index=round_index,
            response=response,
            modifier_calls=[],
            information_calls=[],
        )
        if response.tool_calls:
            log_event(
                level="WARNING",
                event="ai_group_chat.tool_call_when_disabled",
                category="plugin",
                message="工具已被禁用，但模型仍返回了工具调用，已忽略该工具调用",
            )
        return self._build_reply_content(
            content=self._normalize_content(response.content),
            reasoning_content=response.reasoning_content,
        )

    async def _execute_mixed_tool_calls_with_optional_send(
        self,
        *,
        msg: GroupMessage,
        napcat_executor: NapCatGroupToolExecutor,
        working_messages: list[ChatMessage],
        response: LLMResponse,
        reply_content: ReplyContent,
        modifier_calls: list[LLMToolCall],
        information_calls: list[LLMToolCall],
        tool_executor: CompositeToolExecutor,
        sent_content_messages: list[ChatMessage],
    ) -> tuple[list[ChatMessage], bool]:
        """处理同轮同时含消息修饰工具和信息工具的响应。"""
        log_event(
            level="DEBUG",
            event="ai_group_chat.tool_calls.mixed",
            category="plugin",
            message="模型同轮调用了消息修饰工具和信息工具，先发送正文再继续执行信息工具",
            group_id=msg.group_id,
            message_id=msg.message_id,
            modifier_tool_names=[tool_call.name for tool_call in modifier_calls],
            information_tool_names=[tool_call.name for tool_call in information_calls],
        )
        history_messages = self._append_tool_call_response(
            working_messages=working_messages,
            response=response,
            tool_calls=response.tool_calls,
        )
        modifier_history, has_modifier_error = await self._execute_tool_call_results(
            working_messages=working_messages,
            tool_calls=modifier_calls,
            tool_executor=tool_executor,
            group_id=msg.group_id,
        )
        history_messages.extend(modifier_history)
        if has_modifier_error:
            return history_messages, True
        await self._send_reply_content(
            msg=msg,
            reply_content=reply_content,
            napcat_executor=napcat_executor,
            use_modifiers=True,
            sent_content_messages=sent_content_messages,
            event_name="ai_group_chat.reply.sent_with_mixed_tools",
            log_message="模型返回内容并混用工具，已发送带修饰内容后继续执行信息工具",
        )
        information_history, _ = await self._execute_tool_call_results(
            working_messages=working_messages,
            tool_calls=information_calls,
            tool_executor=tool_executor,
            group_id=msg.group_id,
        )
        history_messages.extend(information_history)
        return history_messages, False

    async def _send_reply_content(
        self,
        *,
        msg: GroupMessage,
        reply_content: ReplyContent,
        napcat_executor: NapCatGroupToolExecutor,
        use_modifiers: bool,
        sent_content_messages: list[ChatMessage],
        event_name: str,
        log_message: str,
    ) -> None:
        """只要模型返回正文，就立即发送，并把正文记入长期上下文候选。"""
        if reply_content.visible_content is None:
            return
        if use_modifiers:
            _ = await napcat_executor.send_final_text(reply_content.visible_content)
            napcat_executor.clear_message_modifiers()
        else:
            _ = await self.context.bot.send_msg(
                group_id=msg.group_id,
                text=reply_content.visible_content,
            )
        if reply_content.memory_content is not None:
            sent_content_messages.append(
                ChatMessage(
                    role="assistant",
                    text=reply_content.memory_content,
                    reasoning_content=reply_content.memory_reasoning_content,
                )
            )
        log_event(
            level="DEBUG",
            event=event_name,
            category="plugin",
            message=log_message,
            group_id=msg.group_id,
            message_id=msg.message_id,
            use_modifiers=use_modifiers,
            visible_content_chars=len(reply_content.visible_content),
            memory_content_chars=len(reply_content.memory_content or ""),
            memory_reasoning_chars=len(reply_content.memory_reasoning_content or ""),
        )

    async def _execute_tool_calls(
        self,
        *,
        working_messages: list[ChatMessage],
        response: LLMResponse,
        tool_calls: list[LLMToolCall],
        tool_executor: CompositeToolExecutor,
        group_id: str,
    ) -> tuple[list[ChatMessage], bool]:
        """执行模型请求的工具调用，并把结果写回工作上下文。"""
        history_messages = self._append_tool_call_response(
            working_messages=working_messages,
            response=response,
            tool_calls=tool_calls,
        )
        tool_history, has_error = await self._execute_tool_call_results(
            working_messages=working_messages,
            tool_calls=tool_calls,
            tool_executor=tool_executor,
            group_id=group_id,
        )
        history_messages.extend(tool_history)
        return history_messages, has_error

    def _append_tool_call_response(
        self,
        *,
        working_messages: list[ChatMessage],
        response: LLMResponse,
        tool_calls: list[LLMToolCall],
    ) -> list[ChatMessage]:
        """把带工具调用的 assistant 响应写回当前工作上下文。"""
        assistant_message = ChatMessage(
            role="assistant",
            text=response.content,
            reasoning_content=self._build_memory_reasoning_content(
                response.reasoning_content
            ),
            tool_calls=tool_calls,
        )
        working_messages.append(assistant_message)
        return [assistant_message]

    async def _execute_tool_call_results(
        self,
        *,
        working_messages: list[ChatMessage],
        tool_calls: list[LLMToolCall],
        tool_executor: CompositeToolExecutor,
        group_id: str,
    ) -> tuple[list[ChatMessage], bool]:
        """执行工具调用，并只返回 tool 结果消息。"""
        history_messages: list[ChatMessage] = []
        has_error = False
        for tool_call in tool_calls:
            log_event(
                level="DEBUG",
                event="ai_group_chat.tool_call.start",
                category="plugin",
                message="开始执行模型请求的工具调用",
                group_id=group_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
            tool_message, is_error = await self._call_tool_for_model(
                tool_call=tool_call,
                tool_executor=tool_executor,
            )
            working_messages.append(tool_message)
            history_messages.append(tool_message)
            log_event(
                level="DEBUG",
                event="ai_group_chat.tool_call.finished",
                category="plugin",
                message="模型请求的工具调用执行完成",
                group_id=group_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                is_error=is_error,
                tool_message_chars=len(tool_message.text or ""),
            )
            await self.debug_dumper.append_tool_result(
                group_id=group_id,
                tool_call=tool_call,
                tool_message=tool_message,
                is_error=is_error,
            )
            if is_error:
                has_error = True
        return history_messages, has_error

    async def _call_tool_for_model(
        self,
        *,
        tool_call: LLMToolCall,
        tool_executor: CompositeToolExecutor,
    ) -> tuple[ChatMessage, bool]:
        """调用工具并把成功或失败结果都整理为模型可读的 tool 消息。"""
        result: JsonValue
        try:
            result = await tool_executor.call_tool(
                name=tool_call.name,
                arguments=tool_call.arguments,
            )
        except Exception as exc:
            error_result: JsonObject = {
                "ok": False,
                "error": str(exc),
                "tool_name": tool_call.name,
            }
            result = error_result
        return (
            build_tool_result_message(tool_call_id=tool_call.id, result=result),
            self._is_tool_error_result(result=result),
        )

    def _is_tool_error_result(self, *, result: JsonValue) -> bool:
        """判断工具结果是否表达了失败。"""
        if not isinstance(result, dict):
            return False
        ok_value = result.get("ok")
        if ok_value is False:
            return True
        is_error = result.get("is_error")
        return is_error is True

    def _persist_turn(
        self,
        *,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        sent_content_messages: list[ChatMessage],
        tool_history_messages: list[ChatMessage],
        assistant_content: str | None,
        assistant_reasoning_content: str | None,
        replace_existing_history: bool,
    ) -> None:
        """把本轮用户输入和最终回复写入群上下文。"""
        history_messages = turn_messages[:]
        history_messages.extend(sent_content_messages)
        if self.config.persist_tool_results:
            history_messages.extend(tool_history_messages)
        if assistant_content is not None:
            history_messages.append(
                ChatMessage(
                    role="assistant",
                    text=assistant_content,
                    reasoning_content=assistant_reasoning_content,
                )
            )
        log_event(
            level="DEBUG",
            event="ai_group_chat.context.persist",
            category="plugin",
            message="AI 群聊本轮消息写入长期上下文",
            turn_messages_count=len(turn_messages),
            sent_content_messages_count=len(sent_content_messages),
            tool_history_messages_count=len(tool_history_messages),
            persisted_messages_count=len(history_messages),
            persist_tool_results=self.config.persist_tool_results,
            replace_existing_history=replace_existing_history,
            assistant_content_chars=len(assistant_content or ""),
            assistant_reasoning_chars=len(assistant_reasoning_content or ""),
        )
        if replace_existing_history:
            chat_handler.replace_history(messages=[])
        chat_handler.build_chatmessage(message_lst=history_messages)

    def _normalize_content(self, content: str | None) -> str | None:
        """清理模型文本输出，空白内容视为无回复。"""
        if content is None:
            return None
        stripped_content = content.strip()
        if stripped_content == "":
            return None
        return stripped_content

    def _build_reply_content(
        self, *, content: str | None, reasoning_content: str | None
    ) -> ReplyContent:
        """构造群内可见回复，并保持长期上下文只记录正式回复。"""
        memory_reasoning_content = self._build_memory_reasoning_content(
            reasoning_content
        )
        if content is None:
            return ReplyContent(
                visible_content=None,
                memory_content=None,
                memory_reasoning_content=memory_reasoning_content,
            )
        reasoning_text = self._normalize_content(reasoning_content)
        if not self.config.output_reasoning_content or reasoning_text is None:
            return ReplyContent(
                visible_content=content,
                memory_content=content,
                memory_reasoning_content=memory_reasoning_content,
            )
        visible_content = (
            "【模型原生思维链】\n"
            "---\n"
            f"{reasoning_text}\n"
            "---\n\n"
            "【回复】\n"
            f"{content}"
        )
        return ReplyContent(
            visible_content=visible_content,
            memory_content=content,
            memory_reasoning_content=memory_reasoning_content,
        )

    def _build_memory_reasoning_content(
        self, reasoning_content: str | None
    ) -> str | None:
        """按配置决定是否把模型原生思维链作为结构化字段回传给后续请求。"""
        if not self.config.pass_back_reasoning_content:
            return None
        return self._normalize_content(reasoning_content)
