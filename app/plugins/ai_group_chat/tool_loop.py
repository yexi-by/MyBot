"""AI 群聊插件的工具调用循环。"""

from dataclasses import dataclass

from app.api.mixins.message import NapCatSendMessageError
from app.models import GroupMessage, JsonObject, JsonValue
from app.plugins.base import Context
from app.services import (
    ChatMessage,
    CompositeToolExecutor,
    ContextHandler,
    NapCatGroupToolExecutor,
)
from app.services.llm.schemas import LLMResponse, LLMToolCall, LLMToolDefinition
from app.services.llm.tools import (
    LLMImageArtifact,
    LLMImageError,
    LLMImageItem,
    build_tool_result_message,
)
from app.utils.log import log_event

from .config import AIGroupChatConfig
from .context_compressor import GroupChatContextCompressor
from .debug_dump import AIGroupChatDebugDumper
from .forward_image_auto_fetch import (
    FORWARD_MESSAGE_IMAGES_TOOL_NAME,
    ForwardImageAutoFetcher,
)
from .token_budget import ConservativeTokenEstimator, TokenBudgetEstimate
from .vision_tool import VisionDescriptionTool, VisionTurnState


@dataclass(frozen=True)
class ReplyContent:
    """区分群内可见回复和写入长期上下文的回复。"""

    visible_content: str | None
    memory_content: str | None
    memory_reasoning_content: str | None


@dataclass(frozen=True)
class ReplySendResult:
    """描述群聊回复正文的发送结果。"""

    content_sent: bool
    should_retry_model: bool = False
    failure_status_message: ChatMessage | None = None


@dataclass(frozen=True)
class PreparedTurnContext:
    """描述本轮请求前整理好的工作上下文。"""

    working_messages: list[ChatMessage]
    persisted_turn_messages: list[ChatMessage]
    replace_existing_history: bool


@dataclass(frozen=True)
class ToolCallResultForModel:
    """描述单次工具调用交给主模型的消息和图片旁路结果。"""

    message: ChatMessage
    is_error: bool
    image_items: list[LLMImageItem]
    truncated_image_count: int

    @property
    def image_artifacts_count(self) -> int:
        """返回成功图片数。"""
        return sum(isinstance(item, LLMImageArtifact) for item in self.image_items)

    @property
    def image_errors_count(self) -> int:
        """返回图片错误数。"""
        return sum(isinstance(item, LLMImageError) for item in self.image_items)


class GroupChatToolLoop:
    """执行群聊专用的 OpenAI 工具调用流程。"""

    def __init__(
        self,
        *,
        config: AIGroupChatConfig,
        context: Context,
        debug_dumper: AIGroupChatDebugDumper,
        vision_tool: VisionDescriptionTool,
    ) -> None:
        """保存工具循环所需的配置和运行上下文。"""
        self.config: AIGroupChatConfig = config
        self.context: Context = context
        self.debug_dumper: AIGroupChatDebugDumper = debug_dumper
        self.vision_tool: VisionDescriptionTool = vision_tool
        self.token_estimator: ConservativeTokenEstimator = ConservativeTokenEstimator(
            safety_factor=config.token_estimation_safety_factor
        )
        self.context_compressor: GroupChatContextCompressor = (
            GroupChatContextCompressor()
        )
        self.forward_image_auto_fetcher: ForwardImageAutoFetcher = (
            ForwardImageAutoFetcher(config=config)
        )

    async def run(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        input_vision_messages: list[ChatMessage],
        input_vision_history_messages: list[ChatMessage],
        question: str,
        vision_turn_state: VisionTurnState,
    ) -> None:
        """执行群聊专用工具调用循环。"""
        tool_history_messages: list[ChatMessage] = []
        vision_history_messages: list[ChatMessage] = []
        sent_content_messages: list[ChatMessage] = []
        napcat_executor = NapCatGroupToolExecutor(
            bot=self.context.bot,
            database=self.context.database,
            event=msg,
            allow_mention_all=self.config.allow_mention_all,
            forward_image_tool_enabled=self.config.forward_image_tool_enabled,
            forward_image_max_images_per_call=(
                self.config.forward_image_max_images_per_call
            ),
            forward_image_max_all_images=self.config.forward_image_max_all_images,
            image_fetch_concurrency=self.config.image_fetch_concurrency,
            image_download_timeout_seconds=(
                self.config.image_download_timeout_seconds
            ),
            max_reply_chars=self.config.max_reply_chars,
            http_client=self.context.direct_httpx,
        )
        tool_executor = CompositeToolExecutor(
            [napcat_executor, self.context.mcp_tool_manager]
        )
        tools = tool_executor.list_tools()
        prepared_context = await self._prepare_turn_context(
            msg=msg,
            chat_handler=chat_handler,
            turn_messages=turn_messages,
            input_vision_messages=input_vision_messages,
            input_vision_history_messages=input_vision_history_messages,
            tools=tools,
        )
        working_messages = prepared_context.working_messages
        persisted_turn_messages = prepared_context.persisted_turn_messages
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
            supports_multimodal=self.config.supports_multimodal,
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
                working_messages_count=len(working_messages),
                model_name=self.config.model_name,
                model_vendors=self.config.model_vendors,
                tools_count=len(tools),
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
                tool_call_names=[tool_call.name for tool_call in response.tool_calls],
            )
            content_sent = False
            if reply_content.visible_content is not None:
                send_result = await self._send_reply_content(
                    msg=msg,
                    napcat_executor=napcat_executor,
                    working_messages=working_messages,
                    reply_content=reply_content,
                    sent_content_messages=sent_content_messages,
                    round_index=round_index,
                    event_name="ai_group_chat.reply.sent",
                    log_message="模型返回正文，已解析 content 标记并发送群消息",
                )
                if send_result.should_retry_model:
                    continue
                if send_result.failure_status_message is not None:
                    await self._finish_turn(
                        msg=msg,
                        chat_handler=chat_handler,
                        turn_messages=persisted_turn_messages,
                        sent_content_messages=sent_content_messages,
                        tool_history_messages=tool_history_messages,
                        vision_history_messages=vision_history_messages,
                        replace_existing_history=replace_existing_history,
                        title="群消息发送失败后的长期上下文",
                        failure_status_message=send_result.failure_status_message,
                    )
                    return
                content_sent = send_result.content_sent

            if response.tool_calls:
                await self._handle_tool_response(
                    msg=msg,
                    working_messages=working_messages,
                    response=response,
                    tool_executor=tool_executor,
                    tool_history_messages=tool_history_messages,
                    vision_history_messages=vision_history_messages,
                    round_index=round_index,
                    question=question,
                    vision_turn_state=vision_turn_state,
                )
                continue

            if content_sent:
                await self._finish_turn(
                    msg=msg,
                    chat_handler=chat_handler,
                    turn_messages=persisted_turn_messages,
                    sent_content_messages=sent_content_messages,
                    tool_history_messages=tool_history_messages,
                    vision_history_messages=vision_history_messages,
                    replace_existing_history=replace_existing_history,
                    title="无工具调用自动结束后的长期上下文",
                )
                return

            log_event(
                level="DEBUG",
                event="ai_group_chat.empty_without_tools",
                category="plugin",
                message="模型没有正文，也没有信息工具调用，本轮静默结束",
                group_id=msg.group_id,
                message_id=msg.message_id,
                round_index=round_index,
            )
            await self._finish_turn(
                msg=msg,
                chat_handler=chat_handler,
                turn_messages=persisted_turn_messages,
                sent_content_messages=sent_content_messages,
                tool_history_messages=tool_history_messages,
                vision_history_messages=vision_history_messages,
                replace_existing_history=replace_existing_history,
                title="空响应自动结束后的长期上下文",
            )
            return
        raise RuntimeError(f"AI 群聊工具调用超过最大轮数: {self.config.max_tool_rounds}")

    async def _prepare_turn_context(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        input_vision_messages: list[ChatMessage],
        input_vision_history_messages: list[ChatMessage],
        tools: list[LLMToolDefinition],
    ) -> PreparedTurnContext:
        """在请求模型前按 token 预算决定是否压缩历史上下文。"""
        stored_messages = chat_handler.messages_lst
        stripped_history_image_count = self._count_images(messages=stored_messages)
        history_messages = self._strip_history_images(messages=stored_messages)
        if stripped_history_image_count > 0:
            chat_handler.replace_history(messages=history_messages[1:])
            log_event(
                level="DEBUG",
                event="ai_group_chat.context.history_images_stripped",
                category="plugin",
                message="AI 群聊历史上下文已移除跨轮次图片字节",
                group_id=msg.group_id,
                message_id=msg.message_id,
                stripped_image_count=stripped_history_image_count,
                history_messages_count=len(history_messages),
            )
        current_working_messages = [*turn_messages, *input_vision_messages]
        current_persisted_messages = [
            *turn_messages,
            *input_vision_history_messages,
        ]
        candidate_messages = [*history_messages, *current_working_messages]
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
            turn_messages_count=len(current_working_messages),
            persisted_turn_messages_count=len(current_persisted_messages),
            request_messages_count=len(candidate_messages),
            tools_count=len(tools),
        )
        if not budget.should_compress:
            return PreparedTurnContext(
                working_messages=candidate_messages,
                persisted_turn_messages=current_persisted_messages,
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
        rebuilt_working_user_message = (
            self.context_compressor.build_rebuilt_user_message(
                summary=summary,
                current_turn_messages=current_working_messages,
            )
        )
        rebuilt_persisted_user_message = (
            self.context_compressor.build_rebuilt_user_message(
                summary=summary,
                current_turn_messages=current_persisted_messages,
            )
        )
        rebuilt_working_messages = [
            chat_handler.system_prompt,
            rebuilt_working_user_message,
        ]
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
            rebuilt_user_chars=len(rebuilt_working_user_message.text or ""),
            rebuilt_image_count=len(rebuilt_working_user_message.image or []),
            rebuilt_request_messages_count=len(rebuilt_working_messages),
        )
        if rebuilt_budget.should_compress:
            raise RuntimeError(
                "AI 群聊上下文压缩后仍超过最大上下文预算，"
                f"estimated={rebuilt_budget.estimated_tokens}, "
                f"max={rebuilt_budget.max_context_tokens}"
            )
        return PreparedTurnContext(
            working_messages=rebuilt_working_messages,
            persisted_turn_messages=[rebuilt_persisted_user_message],
            replace_existing_history=True,
        )

    async def _compress_existing_context(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        budget: TokenBudgetEstimate,
    ) -> str:
        """用压缩专用 LLM 请求整理历史上下文，不包含本轮新消息。"""
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
            message="AI 群聊上下文超过预算，开始压缩历史上下文",
            group_id=msg.group_id,
            message_id=msg.message_id,
            estimated_tokens=budget.estimated_tokens,
            max_context_tokens=budget.max_context_tokens,
            history_messages_count=len(history_messages),
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
        log_event(
            level="DEBUG",
            event="ai_group_chat.context_compression.finished",
            category="plugin",
            message="AI 群聊历史上下文压缩完成",
            group_id=msg.group_id,
            message_id=msg.message_id,
            summary_chars=len(normalized_summary),
        )
        return normalized_summary

    async def _handle_tool_response(
        self,
        *,
        msg: GroupMessage,
        working_messages: list[ChatMessage],
        response: LLMResponse,
        tool_executor: CompositeToolExecutor,
        tool_history_messages: list[ChatMessage],
        vision_history_messages: list[ChatMessage],
        round_index: int,
        question: str,
        vision_turn_state: VisionTurnState,
    ) -> None:
        """处理模型请求的信息工具调用，并把工具结果写回本轮工作上下文。"""
        log_event(
            level="DEBUG",
            event="ai_group_chat.tool_response.handle",
            category="plugin",
            message="模型响应包含信息工具调用，开始执行工具并继续本轮",
            group_id=msg.group_id,
            message_id=msg.message_id,
            round_index=round_index,
            tool_names=[tool_call.name for tool_call in response.tool_calls],
        )
        tool_history_messages.extend(
            self._append_tool_call_response(
                working_messages=working_messages,
                response=response,
                tool_calls=response.tool_calls,
            )
        )
        tool_result_history = await self._execute_tool_call_results(
            working_messages=working_messages,
            tool_calls=response.tool_calls,
            tool_executor=tool_executor,
            group_id=msg.group_id,
            vision_history_messages=vision_history_messages,
            question=question,
            vision_turn_state=vision_turn_state,
        )
        tool_history_messages.extend(tool_result_history)

    async def _finish_turn(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        sent_content_messages: list[ChatMessage],
        tool_history_messages: list[ChatMessage],
        vision_history_messages: list[ChatMessage],
        replace_existing_history: bool,
        title: str,
        failure_status_message: ChatMessage | None = None,
    ) -> None:
        """把本次群聊处理落入长期上下文并写入调试快照。"""
        self._persist_turn(
            chat_handler=chat_handler,
            turn_messages=turn_messages,
            sent_content_messages=sent_content_messages,
            tool_history_messages=tool_history_messages,
            vision_history_messages=vision_history_messages,
            replace_existing_history=replace_existing_history,
            failure_status_message=failure_status_message,
        )
        await self.debug_dumper.append_context_snapshot(
            group_id=msg.group_id,
            title=title,
            messages=chat_handler.messages_lst,
        )

    async def _send_reply_content(
        self,
        *,
        msg: GroupMessage,
        reply_content: ReplyContent,
        napcat_executor: NapCatGroupToolExecutor,
        working_messages: list[ChatMessage],
        sent_content_messages: list[ChatMessage],
        round_index: int,
        event_name: str,
        log_message: str,
    ) -> ReplySendResult:
        """只要模型返回正文，就立即发送，并把正文记入长期上下文候选。"""
        if reply_content.visible_content is None:
            return ReplySendResult(content_sent=False)
        try:
            _ = await napcat_executor.send_content(reply_content.visible_content)
        except ValueError as exc:
            working_messages.append(
                ChatMessage(
                    role="user",
                    text=(
                        "你刚才输出的群消息标记格式有误，消息没有发送。"
                        f"错误原因：{exc}。请重新生成一条完整、自然、可以直接发到群里的回复；"
                        "如果需要引用当前消息，用 <Reply>；如果需要艾特某个 QQ，用 <At>QQ号</At>。"
                    ),
                )
            )
            log_event(
                level="WARNING",
                event="ai_group_chat.content_directive.invalid",
                category="plugin",
                message="模型输出的群消息标记无效，已要求模型重写",
                group_id=msg.group_id,
                message_id=msg.message_id,
                round_index=round_index,
                error=str(exc),
                content_chars=len(reply_content.visible_content),
            )
            return ReplySendResult(content_sent=False, should_retry_model=True)
        except NapCatSendMessageError as exc:
            failure_status_message = self._build_send_failure_status_message(
                error=exc
            )
            log_event(
                level="ERROR",
                event="ai_group_chat.reply.send_failed",
                category="plugin",
                message="模型回复发送到群聊失败，已结束本轮并写入运行状态上下文",
                group_id=msg.group_id,
                message_id=msg.message_id,
                round_index=round_index,
                error_type=type(exc).__name__,
                error=str(exc),
                content_chars=len(reply_content.visible_content),
            )
            return ReplySendResult(
                content_sent=False,
                failure_status_message=failure_status_message,
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
            visible_content_chars=len(reply_content.visible_content),
            memory_content_chars=len(reply_content.memory_content or ""),
            memory_reasoning_chars=len(reply_content.memory_reasoning_content or ""),
        )
        return ReplySendResult(content_sent=True)

    def _build_send_failure_status_message(
        self, *, error: NapCatSendMessageError
    ) -> ChatMessage:
        """构造发送失败时写入长期上下文的运行状态消息。"""
        return ChatMessage(
            role="system",
            text=(
                "运行状态：上一条 AI 群聊回复没有发送到群内。"
                f"发送层错误：{error}"
            ),
        )

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
        vision_history_messages: list[ChatMessage],
        question: str,
        vision_turn_state: VisionTurnState,
    ) -> list[ChatMessage]:
        """执行工具调用，并只返回 tool 结果消息。"""
        history_messages: list[ChatMessage] = []
        image_items: list[LLMImageItem] = []
        truncated_image_count = 0
        explicit_forward_image_call = any(
            tool_call.name == FORWARD_MESSAGE_IMAGES_TOOL_NAME
            for tool_call in tool_calls
        )
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
            outcome = await self._call_tool_for_model(
                tool_call=tool_call,
                tool_executor=tool_executor,
                group_id=group_id,
                explicit_forward_image_call=explicit_forward_image_call,
            )
            working_messages.append(outcome.message)
            history_messages.append(outcome.message)
            image_items.extend(outcome.image_items)
            truncated_image_count += outcome.truncated_image_count
            log_event(
                level="DEBUG",
                event="ai_group_chat.tool_call.finished",
                category="plugin",
                message="模型请求的工具调用执行完成",
                group_id=group_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                is_error=outcome.is_error,
                tool_message_chars=len(outcome.message.text or ""),
                image_artifacts_count=outcome.image_artifacts_count,
                image_errors_count=outcome.image_errors_count,
                truncated_image_count=outcome.truncated_image_count,
            )
        if image_items or truncated_image_count > 0:
            delivery = await self.vision_tool.deliver(
                items=image_items,
                truncated_count=truncated_image_count,
                question=question,
                source_name=(
                    "工具 "
                    + ", ".join(tool_call.name for tool_call in tool_calls)
                    + " 返回的图片"
                ),
                turn_state=vision_turn_state,
            )
            working_messages.extend(delivery.working_messages)
            vision_history_messages.extend(delivery.history_messages)
            log_event(
                level=(
                    "WARNING"
                    if delivery.result is not None and delivery.result.is_error
                    else "DEBUG"
                ),
                event="ai_group_chat.tool_images.delivered",
                category="plugin",
                message="工具图片已通过内部视觉服务交给主模型",
                group_id=group_id,
                vision_ok=(
                    delivery.result.ok if delivery.result is not None else None
                ),
                vision_is_error=(
                    delivery.result.is_error
                    if delivery.result is not None
                    else None
                ),
                observed_count=(
                    delivery.result.observed_count
                    if delivery.result is not None
                    else 0
                ),
                truncated_count=(
                    delivery.result.truncated_count
                    if delivery.result is not None
                    else 0
                ),
                errors_count=(
                    len(delivery.result.errors)
                    if delivery.result is not None
                    else 0
                ),
            )
        return history_messages

    async def _call_tool_for_model(
        self,
        *,
        tool_call: LLMToolCall,
        tool_executor: CompositeToolExecutor,
        group_id: str,
        explicit_forward_image_call: bool,
    ) -> ToolCallResultForModel:
        """调用工具并把成功或失败结果都整理为模型可读的 tool 消息。"""
        result: JsonValue
        image_items: list[LLMImageItem] = []
        truncated_image_count = 0
        try:
            execution_result = await tool_executor.call_tool_with_artifacts(
                name=tool_call.name,
                arguments=tool_call.arguments,
            )
            result = execution_result.result
            image_items = list(execution_result.image_items)
            truncated_image_count = execution_result.truncated_image_count
            if self.forward_image_auto_fetcher.should_fetch(
                tool_call=tool_call,
                result=result,
                explicit_forward_image_call=explicit_forward_image_call,
            ):
                auto_image_result = await self.forward_image_auto_fetcher.fetch(
                    forward_result=result,
                    tool_executor=tool_executor,
                    group_id=group_id,
                )
                result = self.forward_image_auto_fetcher.merge_result(
                    forward_result=result,
                    image_result=auto_image_result.result,
                )
                image_items.extend(auto_image_result.image_items)
                truncated_image_count += auto_image_result.truncated_image_count
        except Exception as exc:
            error_result: JsonObject = {
                "ok": False,
                "error": str(exc),
                "tool_name": tool_call.name,
            }
            result = error_result
        return ToolCallResultForModel(
            message=build_tool_result_message(
                tool_call_id=tool_call.id,
                result=result,
            ),
            is_error=self._is_tool_error_result(result=result),
            image_items=image_items,
            truncated_image_count=truncated_image_count,
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
        vision_history_messages: list[ChatMessage],
        replace_existing_history: bool,
        failure_status_message: ChatMessage | None = None,
    ) -> None:
        """把本轮用户输入和最终回复写入群上下文。"""
        stripped_turn_image_count = self._count_images(messages=turn_messages)
        sanitized_turn_messages = self._strip_history_images(messages=turn_messages)
        history_messages = sanitized_turn_messages[:]
        if self.config.persist_tool_results:
            history_messages.extend(tool_history_messages)
        history_messages.extend(vision_history_messages)
        history_messages.extend(sent_content_messages)
        if failure_status_message is not None:
            history_messages.append(failure_status_message)
        log_event(
            level="DEBUG",
            event="ai_group_chat.context.persist",
            category="plugin",
            message="AI 群聊本轮消息写入长期上下文",
            turn_messages_count=len(turn_messages),
            stripped_turn_image_count=stripped_turn_image_count,
            sent_content_messages_count=len(sent_content_messages),
            tool_history_messages_count=len(tool_history_messages),
            vision_history_messages_count=len(vision_history_messages),
            persisted_messages_count=len(history_messages),
            persist_tool_results=self.config.persist_tool_results,
            persist_vision_descriptions=self.config.persist_vision_descriptions,
            replace_existing_history=replace_existing_history,
        )
        if replace_existing_history:
            chat_handler.replace_history(messages=[])
        chat_handler.build_chatmessage(message_lst=history_messages)

    def _strip_history_images(self, *, messages: list[ChatMessage]) -> list[ChatMessage]:
        """生成不含图片字节的历史消息，避免图片跨轮次进入普通模型请求。"""
        return [
            self._strip_message_images(message=message)
            for message in messages
        ]

    def _strip_message_images(self, *, message: ChatMessage) -> ChatMessage:
        """复制聊天消息并移除只应服务于本轮请求的图片字节。"""
        if not message.image:
            return message
        text = message.text
        if text is None:
            text = "（图片内容已用于当轮多模态请求，长期上下文不保存图片字节）"
        return ChatMessage(
            role=message.role,
            text=text,
            reasoning_content=message.reasoning_content,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
        )

    def _count_images(self, *, messages: list[ChatMessage]) -> int:
        """统计消息列表中仍携带的图片字节数量。"""
        return sum(len(message.image or []) for message in messages)

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
