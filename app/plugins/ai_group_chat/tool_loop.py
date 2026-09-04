"""AI 群聊插件的工具调用循环。"""

from dataclasses import dataclass, field

from app.api.mixins.message import NapCatSendMessageError
from app.config import AIGroupChatConfig
from app.models import GroupMessage, JsonObject, JsonValue
from app.plugins.base import Context
from app.services import (
    ChatMessage,
    CompositeToolExecutor,
    ContextHandler,
    NapCatGroupToolExecutor,
)
from app.services.llm.schemas import LLMResponse, LLMToolCall, LLMToolDefinition
from app.services.llm.errors import LLMRequestError
from app.services.llm.tools import (
    LLMImageArtifact,
    LLMImageError,
    LLMImageItem,
    build_tool_result_message,
)
from app.services.napcat.message_formatter import NapCatMessageTextFormatter
from app.utils.log import log_event

from .context_compressor import GroupChatContextCompressor
from .forward_image_auto_fetch import (
    FORWARD_MESSAGE_IMAGES_TOOL_NAME,
    ForwardImageAutoFetcher,
)
from .token_budget import ConservativeTokenEstimator, TokenBudgetEstimate
from .vision_tool import VisionDescriptionTool, VisionTurnState


class GroupChatTurnError(RuntimeError):
    """本轮超过可用预算或工具轮数，需要向群成员说明结束原因。"""


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
    fallback_turn_messages: list[ChatMessage]
    replace_existing_history: bool


@dataclass
class TurnContextState:
    """记录一次临时上下文完成后需要提交的边界和统计。"""

    fallback_turn_messages: list[ChatMessage] = field(default_factory=list)
    persisted_prefix_count: int | None = None
    replace_existing_history: bool = False
    commit_requested: bool = False
    title: str = "异常结束后的长期上下文"
    turn_messages_count: int = 0
    sent_content_messages_count: int = 0
    tool_history_messages_count: int = 0
    retained_tool_messages_count: int = 0
    vision_history_messages_count: int = 0


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


@dataclass(frozen=True)
class ToolCallStatus:
    """描述允许进入长期上下文的有界工具调用状态。"""

    name: str
    is_error: bool


class GroupChatToolLoop:
    """执行群聊专用的 OpenAI 工具调用流程。"""

    def __init__(
        self,
        *,
        config: AIGroupChatConfig,
        context: Context,
        vision_tool: VisionDescriptionTool,
    ) -> None:
        """保存工具循环所需的配置和运行上下文。"""
        self.config: AIGroupChatConfig = config
        self.context: Context = context
        self.vision_tool: VisionDescriptionTool = vision_tool
        token_config = config.token_estimator
        self.token_estimator: ConservativeTokenEstimator = ConservativeTokenEstimator(
            safety_factor=config.token_safety_factor,
            request_overhead_tokens=token_config.request_overhead_tokens,
            message_overhead_tokens=token_config.message_overhead_tokens,
            tool_call_overhead_tokens=token_config.tool_call_overhead_tokens,
            image_tokens=token_config.image_tokens,
            ascii_tokens_per_character=token_config.ascii_tokens_per_character,
            non_ascii_tokens_per_character=(
                token_config.non_ascii_tokens_per_character
            ),
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
        turn_context_state: TurnContextState | None = None,
    ) -> None:
        """执行群聊专用工具调用循环。"""
        context_state = turn_context_state or TurnContextState()
        tool_history_messages: list[ChatMessage] = []
        vision_history_messages: list[ChatMessage] = []
        sent_content_messages_count = 0
        retained_tool_messages_count = 0
        formatting = self.config.formatting
        message_formatter = NapCatMessageTextFormatter(
            field_text_limit=formatting.field_text_limit,
            json_text_limit=formatting.json_text_limit,
            markdown_text_limit=formatting.markdown_text_limit,
            forward_max_items=formatting.forward_max_items,
            forward_max_depth=formatting.forward_max_depth,
            nested_text_search_max_depth=formatting.nested_text_search_max_depth,
        )
        napcat_executor = NapCatGroupToolExecutor(
            bot=self.context.bot,
            group_messages=self.context.group_messages,
            event=msg,
            allow_mention_all=self.config.allow_mention_all,
            forward_image_tool_enabled=self.config.images.forward_tool_enabled,
            forward_image_max_images_per_call=(
                self.config.images.forward_max_per_call
            ),
            forward_image_max_images_per_turn=(
                self.config.images.forward_max_per_turn
            ),
            image_fetch_concurrency=self.config.images.fetch_concurrency,
            image_download_timeout_seconds=(
                self.config.images.download_timeout_seconds
            ),
            image_max_bytes=(
                None
                if self.config.images.oversize_behavior == "describe"
                else self.config.images.max_image_bytes or None
            ),
            forward_reply_threshold_chars=(
                self.config.forward_reply_threshold_chars
            ),
            http_client=self.context.direct_httpx,
            message_formatter=message_formatter,
            history_default_limit=self.config.history.default_limit,
            history_max_per_call=self.config.history.max_per_call,
            history_default_before_count=(
                self.config.history.default_before_count
            ),
            history_default_after_count=self.config.history.default_after_count,
            file_default_count=self.config.files.default_count,
            file_max_per_call=self.config.files.max_per_call,
            remaining_image_delivery_slots=lambda: (
                None
                if self.config.images.max_per_turn == 0
                else max(
                    0,
                    self.config.images.max_per_turn
                    - vision_turn_state.consumed_image_slots,
                )
            ),
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
        if self.config.images.delivery_mode == "direct":
            vision_turn_state.consumed_image_bytes = sum(
                len(image)
                for message in working_messages
                for image in message.image or []
            )
        persisted_turn_messages = prepared_context.persisted_turn_messages
        replace_existing_history = prepared_context.replace_existing_history
        self._persist_turn_input(
            chat_handler=chat_handler,
            turn_messages=persisted_turn_messages,
            replace_existing_history=replace_existing_history,
        )
        context_state.fallback_turn_messages = (
            prepared_context.fallback_turn_messages
        )
        context_state.persisted_prefix_count = len(chat_handler.messages_lst)
        context_state.replace_existing_history = replace_existing_history
        log_event(
            level="DEBUG",
            event="ai_group_chat.tool_loop.start",
            category="plugin",
            message="AI 群聊工具循环开始",
            group_id=msg.group_id,
            message_id=msg.message_id,
            model_name=self.config.model.name,
            provider=self.config.model.provider,
            supports_images=self.config.model.supports_images,
            image_delivery_mode=self.config.images.delivery_mode,
            working_messages_count=len(working_messages),
            tools_count=len(tools),
            tool_names=[tool.name for tool in tools],
        )
        snapshot_title = "异常结束后的长期上下文"
        try:
            for round_index in range(1, self.config.max_tool_rounds + 1):
                self._require_request_budget(
                    messages=working_messages, tools=tools,
                    max_context_tokens=chat_handler.max_context_tokens,
                )
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
                    model_name=self.config.model.name,
                    provider=self.config.model.provider,
                    tools_count=len(tools),
                    tool_choice="auto",
                )
                response = await self.context.llm.get_ai_response_with_tools(
                    messages=working_messages,
                    provider=self.config.model.provider,
                    model_name=self.config.model.name,
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
                    tool_call_names=[
                        tool_call.name for tool_call in response.tool_calls
                    ],
                )
                content_sent = False
                if reply_content.visible_content is not None:
                    send_result = await self._send_reply_content(
                        msg=msg,
                        napcat_executor=napcat_executor,
                        chat_handler=chat_handler,
                        working_messages=working_messages,
                        reply_content=reply_content,
                        round_index=round_index,
                        event_name="ai_group_chat.reply.sent",
                        log_message="模型返回正文，已解析 content 标记并发送群消息",
                    )
                    if send_result.should_retry_model:
                        continue
                    if send_result.failure_status_message is not None:
                        chat_handler.build_chatmessage(
                            message=send_result.failure_status_message
                        )
                        snapshot_title = "群消息发送失败后的长期上下文"
                        context_state.commit_requested = True
                        return
                    content_sent = send_result.content_sent
                    if content_sent:
                        sent_content_messages_count += 1

                if response.tool_calls:
                    napcat_executor.begin_image_delivery_batch()
                    retained_tool_messages_count += await self._handle_tool_response(
                        msg=msg,
                        chat_handler=chat_handler,
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
                    snapshot_title = "无工具调用自动结束后的长期上下文"
                    context_state.commit_requested = True
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
                snapshot_title = "空响应自动结束后的长期上下文"
                context_state.commit_requested = True
                return
            snapshot_title = "工具轮数耗尽后的长期上下文"
            raise GroupChatTurnError(
                f"AI 群聊工具调用超过最大轮数: {self.config.max_tool_rounds}"
            )
        finally:
            context_state.title = snapshot_title
            context_state.turn_messages_count = len(persisted_turn_messages)
            context_state.sent_content_messages_count = sent_content_messages_count
            if sent_content_messages_count > 0:
                context_state.commit_requested = True
            context_state.tool_history_messages_count = len(tool_history_messages)
            context_state.retained_tool_messages_count = retained_tool_messages_count
            context_state.vision_history_messages_count = len(
                vision_history_messages
            )

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
        if self.config.images.retain_images:
            history_messages = list(stored_messages)
            stripped_history_image_count = 0
        else:
            stripped_history_image_count = self._count_images(
                messages=stored_messages
            )
            history_messages = self._strip_history_images(messages=stored_messages)
        current_working_messages = [*turn_messages, *input_vision_messages]
        history_messages, byte_limit_stripped_count = (
            self._fit_history_images_to_byte_limit(
                history_messages=history_messages,
                current_messages=current_working_messages,
            )
        )
        stripped_history_image_count += byte_limit_stripped_count
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
        current_persisted_messages = [
            *turn_messages,
            *input_vision_history_messages,
        ]
        fallback_turn_messages = (
            list(current_persisted_messages)
            if self.config.images.retain_images
            else self._strip_history_images(messages=current_persisted_messages)
        )
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
                fallback_turn_messages=fallback_turn_messages,
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
            current_turn_messages=current_working_messages,
            tools=tools,
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
            raise GroupChatTurnError(
                "AI 群聊上下文压缩后仍超过最大上下文预算，"
                f"estimated={rebuilt_budget.estimated_tokens}, "
                f"max={rebuilt_budget.max_context_tokens}"
            )
        return PreparedTurnContext(
            working_messages=rebuilt_working_messages,
            persisted_turn_messages=[rebuilt_persisted_user_message],
            fallback_turn_messages=fallback_turn_messages,
            replace_existing_history=True,
        )

    async def _compress_existing_context(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        budget: TokenBudgetEstimate,
        current_turn_messages: list[ChatMessage],
        tools: list[LLMToolDefinition],
    ) -> str:
        """用压缩专用 LLM 请求整理历史上下文，不包含本轮新消息。"""
        history_messages = chat_handler.messages_lst[1:]
        compression_input = self.context_compressor.format_history(messages=history_messages)
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
        max_tokens = chat_handler.max_context_tokens

        def compression_request(text: str) -> list[ChatMessage]:
            return self.context_compressor.build_compression_messages(
                system_prompt=chat_handler.system_prompt, formatted_context=text,
            )

        def reply_budget(summary: str) -> TokenBudgetEstimate:
            rebuilt = self.context_compressor.build_rebuilt_user_message(
                summary=summary, current_turn_messages=current_turn_messages,
            )
            return self.token_estimator.check_request(
                messages=[chat_handler.system_prompt, rebuilt], tools=tools,
                max_context_tokens=max_tokens,
            )

        if reply_budget("").should_compress:
            raise GroupChatTurnError(
                "系统提示词、工具和当前问题已占满上下文预算，请缩小问题或增大当前群的 max_context_tokens。"
            )
        pending_text = compression_input.formatted_context
        request_count = 0
        while True:
            summaries: list[str] = []
            position = 0
            while position < len(pending_text) or not summaries:
                end = len(pending_text)
                request = compression_request(pending_text[position:end])
                if self.token_estimator.estimate_request(messages=request, tools=[]) > max_tokens:
                    # 在实际请求（含提示词）上找出能容纳的最大连续片段，单条长消息同样可拆分。
                    low, high = position, end
                    while low < high:
                        middle = (low + high + 1) // 2
                        candidate = compression_request(pending_text[position:middle])
                        if self.token_estimator.estimate_request(messages=candidate, tools=[]) <= max_tokens:
                            low = middle
                        else:
                            high = middle - 1
                    if low == position:
                        raise GroupChatTurnError("系统提示词与压缩要求已占满预算，请增大当前群的 max_context_tokens。")
                    end = low
                    request = compression_request(pending_text[position:end])
                self._require_request_budget(messages=request, tools=[], max_context_tokens=max_tokens)
                response = await self.context.llm.get_ai_text_response(
                    messages=request, provider=self.config.model.provider, model_name=self.config.model.name,
                )
                summary = self._normalize_content(response)
                if summary is None:
                    raise LLMRequestError("模型返回了空历史摘要，本轮已停止，请稍后重试。")
                summaries.append(summary)
                request_count += 1
                position = end
            normalized_summary = "\n\n".join(summaries)
            if not reply_budget(normalized_summary).should_compress:
                break
            previous_tokens = self.token_estimator.estimate_request(messages=compression_request(pending_text), tools=[])
            reduced_tokens = self.token_estimator.estimate_request(messages=compression_request(normalized_summary), tools=[])
            if reduced_tokens >= previous_tokens:
                raise LLMRequestError("模型生成的历史摘要未能缩短内容，请调整模型或上下文预算后重试。")
            pending_text = normalized_summary
        log_event(
            level="DEBUG",
            event="ai_group_chat.context_compression.finished",
            category="plugin",
            message="AI 群聊历史上下文压缩完成",
            group_id=msg.group_id,
            message_id=msg.message_id,
            summary_chars=len(normalized_summary),
            request_count=request_count,
        )
        return normalized_summary

    async def _handle_tool_response(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        working_messages: list[ChatMessage],
        response: LLMResponse,
        tool_executor: CompositeToolExecutor,
        tool_history_messages: list[ChatMessage],
        vision_history_messages: list[ChatMessage],
        round_index: int,
        question: str,
        vision_turn_state: VisionTurnState,
    ) -> int:
        """把原始工具结果留在本轮，并按配置决定长期保存形式。"""
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
        history_start = len(tool_history_messages)
        tool_history_messages.extend(
            self._append_tool_call_response(
                working_messages=working_messages,
                response=response,
                tool_calls=response.tool_calls,
            )
        )
        previous_vision_count = len(vision_history_messages)
        tool_result_history, tool_statuses = await self._execute_tool_call_results(
            working_messages=working_messages,
            tool_calls=response.tool_calls,
            tool_executor=tool_executor,
            group_id=msg.group_id,
            vision_history_messages=vision_history_messages,
            question=question,
            vision_turn_state=vision_turn_state,
        )
        self._limit_tool_results(
            working_messages=working_messages,
            tool_messages=tool_result_history,
            statuses=tool_statuses,
            tools=tool_executor.list_tools(),
            max_context_tokens=chat_handler.max_context_tokens,
        )
        tool_history_messages.extend(tool_result_history)
        retained_count = 0
        if self.config.tool_result_retention == "summary" and tool_statuses:
            chat_handler.build_chatmessage(
                message=self._build_tool_status_summary(statuses=tool_statuses)
            )
            retained_count = 1
        elif self.config.tool_result_retention == "full":
            retained_messages = tool_history_messages[history_start:]
            if retained_messages:
                chat_handler.build_chatmessage(message_lst=retained_messages)
                retained_count = len(retained_messages)
        new_vision_messages = vision_history_messages[previous_vision_count:]
        if new_vision_messages:
            chat_handler.build_chatmessage(message_lst=new_vision_messages)
        return retained_count

    def _require_request_budget(
        self, *, messages: list[ChatMessage], tools: list[LLMToolDefinition],
        max_context_tokens: int,
    ) -> None:
        """每次模型请求都遵守当前群的完整上下文预算。"""
        budget = self.token_estimator.check_request(
            messages=messages, tools=tools, max_context_tokens=max_context_tokens,
        )
        if budget.should_compress:
            raise GroupChatTurnError(
                f"本轮内容超过上下文预算（估算 {budget.estimated_tokens}，上限 {max_context_tokens}），"
                "请缩小查询范围或分次提问。"
            )

    def _limit_tool_results(
        self, *, working_messages: list[ChatMessage], tool_messages: list[ChatMessage],
        statuses: list[ToolCallStatus], tools: list[LLMToolDefinition],
        max_context_tokens: int,
    ) -> None:
        """把装不进本轮预算的大工具结果改为可恢复错误，保留调用配对。"""
        costs = [
            self.token_estimator.estimate_request(messages=[message], tools=[])
            for message in tool_messages
        ]
        for index in sorted(
            range(len(tool_messages)), key=costs.__getitem__, reverse=True,
        ):
            budget = self.token_estimator.check_request(
                messages=working_messages, tools=tools, max_context_tokens=max_context_tokens,
            )
            if not budget.should_compress:
                return
            message = tool_messages[index]
            replacement = build_tool_result_message(
                tool_call_id=message.tool_call_id or "",
                result={
                    "ok": False,
                    "is_error": True,
                    "error_type": "ToolResultTooLarge",
                    "error": "工具返回内容超过本轮上下文预算，结果未交付。",
                    "message": "请减小 limit、时间范围或选取的内容，分次查询；也可以依据已有信息完成回答。",
                },
            )
            if costs[index] <= self.token_estimator.estimate_request(messages=[replacement], tools=[]):
                continue
            # 这些消息仅属于刚完成的本批工具，working_messages 和待保存记录共享它们。
            message.text = replacement.text
            statuses[index] = ToolCallStatus(name=statuses[index].name, is_error=True)

    def _persist_turn_input(
        self,
        *,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        replace_existing_history: bool,
    ) -> None:
        """在请求模型前提交本轮用户输入和输入图片描述。"""
        sanitized_messages = (
            list(turn_messages)
            if self.config.images.retain_images
            else self._strip_history_images(messages=turn_messages)
        )
        if replace_existing_history:
            chat_handler.replace_history(messages=[])
        chat_handler.build_chatmessage(message_lst=sanitized_messages)

    async def _send_reply_content(
        self,
        *,
        msg: GroupMessage,
        reply_content: ReplyContent,
        napcat_executor: NapCatGroupToolExecutor,
        chat_handler: ContextHandler,
        working_messages: list[ChatMessage],
        round_index: int,
        event_name: str,
        log_message: str,
    ) -> ReplySendResult:
        """发送模型正文，并在成功返回后立即提交 assistant 上下文。"""
        if reply_content.visible_content is None:
            return ReplySendResult(content_sent=False)
        memory_message = (
            ChatMessage(
                role="assistant",
                text=reply_content.memory_content,
                reasoning_content=reply_content.memory_reasoning_content,
            )
            if reply_content.memory_content is not None
            else None
        )
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
        if memory_message is not None:
            chat_handler.build_chatmessage(message=memory_message)
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
            reasoning_content=self._normalize_content(response.reasoning_content),
            tool_calls=tool_calls,
        )
        working_messages.append(assistant_message)
        return [
            ChatMessage(
                role="assistant",
                reasoning_content=(
                    self._normalize_content(response.reasoning_content)
                    if self.config.retain_reasoning
                    else None
                ),
                tool_calls=tool_calls,
            )
        ]

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
    ) -> tuple[list[ChatMessage], list[ToolCallStatus]]:
        """执行工具调用，并返回本轮原始结果与有界状态。"""
        history_messages: list[ChatMessage] = []
        statuses: list[ToolCallStatus] = []
        image_items: list[LLMImageItem] = []
        truncated_image_count = 0
        explicit_forward_image_message_ids = {
            message_id
            for tool_call in tool_calls
            if tool_call.name == FORWARD_MESSAGE_IMAGES_TOOL_NAME
            for message_id in [tool_call.arguments.get("message_id")]
            if isinstance(message_id, str)
        }
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
                explicit_forward_image_message_ids=(
                    explicit_forward_image_message_ids
                ),
            )
            working_messages.append(outcome.message)
            history_messages.append(outcome.message)
            statuses.append(
                ToolCallStatus(name=tool_call.name, is_error=outcome.is_error)
            )
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
        return history_messages, statuses

    def _build_tool_status_summary(
        self, *, statuses: list[ToolCallStatus]
    ) -> ChatMessage:
        """按工具名聚合成功与失败次数，不保存参数和原始结果。"""
        counts_by_name: dict[str, list[int]] = {}
        for status in statuses:
            counts = counts_by_name.setdefault(status.name, [0, 0])
            counts[1 if status.is_error else 0] += 1
        parts: list[str] = []
        for name, (success_count, error_count) in counts_by_name.items():
            status_parts: list[str] = []
            if success_count > 0:
                status_parts.append(f"成功 {success_count} 次")
            if error_count > 0:
                status_parts.append(f"失败 {error_count} 次")
            parts.append(f"{name}（{'，'.join(status_parts)}）")
        return ChatMessage(
            role="system",
            text="运行记录：本轮调用了信息工具：" + "；".join(parts) + "。",
        )

    async def _call_tool_for_model(
        self,
        *,
        tool_call: LLMToolCall,
        tool_executor: CompositeToolExecutor,
        group_id: str,
        explicit_forward_image_message_ids: set[str],
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
                explicit_forward_image_message_ids=(
                    explicit_forward_image_message_ids
                ),
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

    def _strip_history_images(self, *, messages: list[ChatMessage]) -> list[ChatMessage]:
        """生成不含图片字节的历史消息，避免图片跨轮次进入普通模型请求。"""
        return [
            message.without_images()
            for message in messages
        ]

    def _count_images(self, *, messages: list[ChatMessage]) -> int:
        """统计消息列表中仍携带的图片字节数量。"""
        return sum(len(message.image or []) for message in messages)

    def _fit_history_images_to_byte_limit(
        self,
        *,
        history_messages: list[ChatMessage],
        current_messages: list[ChatMessage],
    ) -> tuple[list[ChatMessage], int]:
        """需要时从最旧历史开始移除图片，使当前请求符合总字节配置。"""
        byte_limit = self.config.images.max_total_bytes_per_request
        if byte_limit == 0:
            return history_messages, 0
        current_bytes = sum(
            len(image)
            for message in current_messages
            for image in message.image or []
        )
        history_bytes = sum(
            len(image)
            for message in history_messages
            for image in message.image or []
        )
        if current_bytes + history_bytes <= byte_limit:
            return history_messages, 0
        if self.config.images.oversize_behavior == "error":
            raise GroupChatTurnError(
                "历史与当前图片总字节超过 images.max_total_bytes_per_request"
            )
        fitted = list(history_messages)
        stripped_count = 0
        for index, message in enumerate(fitted):
            if current_bytes + history_bytes <= byte_limit:
                break
            if not message.image:
                continue
            history_bytes -= sum(len(image) for image in message.image)
            stripped_count += len(message.image)
            fitted[index] = message.without_images()
        return fitted, stripped_count

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
        if not self.config.show_reasoning or reasoning_text is None:
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
        """按配置决定是否把模型 reasoning 保存到下一次用户请求。"""
        if not self.config.retain_reasoning:
            return None
        return self._normalize_content(reasoning_content)
