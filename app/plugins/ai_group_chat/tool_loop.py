"""AI 群聊插件的工具调用循环。"""

from dataclasses import dataclass

from app.models import GroupMessage, JsonObject, JsonValue
from app.plugins.base import Context
from app.services import ChatMessage, CompositeToolExecutor, ContextHandler, NapCatGroupToolExecutor
from app.services.llm.schemas import LLMResponse, LLMToolCall, LLMToolDefinition
from app.services.llm.tools import build_tool_result_message
from app.utils.log import log_event

from .config import AIGroupChatConfig


@dataclass(frozen=True)
class ReplyContent:
    """区分群内可见回复和写入长期上下文的回复。"""

    visible_content: str | None
    memory_content: str | None
    memory_reasoning_content: str | None


class GroupChatToolLoop:
    """执行群聊专用的 OpenAI 工具调用流程。"""

    def __init__(self, *, config: AIGroupChatConfig, context: Context) -> None:
        """保存工具循环所需的配置和运行上下文。"""
        self.config: AIGroupChatConfig = config
        self.context: Context = context

    async def run(
        self,
        *,
        msg: GroupMessage,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
    ) -> None:
        """执行群聊专用工具调用循环。"""
        working_messages = chat_handler.messages_lst
        working_messages.extend(turn_messages)
        tool_history_messages: list[ChatMessage] = []
        corrections_count = 0
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
        for _ in range(self.config.max_tool_rounds):
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
            if not response.tool_calls:
                await self._finish_plain_response(
                    msg=msg,
                    chat_handler=chat_handler,
                    turn_messages=turn_messages,
                    tool_history_messages=tool_history_messages,
                    visible_content=reply_content.visible_content,
                    memory_content=reply_content.memory_content,
                    memory_reasoning_content=reply_content.memory_reasoning_content,
                )
                return
            if modifier_calls and information_calls:
                corrections_count = self._append_correction(
                    working_messages=working_messages,
                    corrections_count=corrections_count,
                    reason=(
                        "你同一轮同时调用了 QQ 消息修饰工具和信息工具。"
                        "请先只调用信息工具，拿到结果后再输出最终回复。"
                    ),
                )
                continue
            if modifier_calls:
                modifier_history, has_modifier_error = await self._execute_tool_calls(
                    working_messages=working_messages,
                    response=response,
                    tool_calls=modifier_calls,
                    tool_executor=tool_executor,
                )
                tool_history_messages.extend(modifier_history)
                if has_modifier_error:
                    napcat_executor.clear_message_modifiers()
                    continue
                if reply_content.visible_content is None:
                    log_event(
                        level="WARNING",
                        event="ai_group_chat.modifier_without_content",
                        category="plugin",
                        message="模型调用了 QQ 消息修饰工具但没有输出正文，已转入二次正文生成",
                    )
                    reply_content = await self._request_final_content_after_modifiers(
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
                        tool_history_messages=tool_history_messages,
                        assistant_content=None,
                        assistant_reasoning_content=None,
                    )
                    return
                _ = await napcat_executor.send_final_text(reply_content.visible_content)
                self._persist_turn(
                    chat_handler=chat_handler,
                    turn_messages=turn_messages,
                    tool_history_messages=tool_history_messages,
                    assistant_content=reply_content.memory_content,
                    assistant_reasoning_content=reply_content.memory_reasoning_content,
                )
                return
            information_history, _ = await self._execute_tool_calls(
                working_messages=working_messages,
                response=response,
                tool_calls=information_calls,
                tool_executor=tool_executor,
            )
            tool_history_messages.extend(information_history)
        raise RuntimeError(f"AI 群聊工具调用超过最大轮数: {self.config.max_tool_rounds}")

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
        tool_history_messages: list[ChatMessage],
        visible_content: str | None,
        memory_content: str | None,
        memory_reasoning_content: str | None,
    ) -> None:
        """处理没有工具调用的最终响应。"""
        if visible_content is not None:
            _ = await self.context.bot.send_msg(
                group_id=msg.group_id, text=visible_content
            )
        self._persist_turn(
            chat_handler=chat_handler,
            turn_messages=turn_messages,
            tool_history_messages=tool_history_messages,
            assistant_content=memory_content,
            assistant_reasoning_content=memory_reasoning_content,
        )

    async def _request_final_content_after_modifiers(
        self,
        *,
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
        response = await self.context.llm.get_ai_response_with_tools(
            messages=working_messages,
            model_vendors=self.config.model_vendors,
            model_name=self.config.model_name,
            tools=tools,
            tool_choice="none",
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

    async def _execute_tool_calls(
        self,
        *,
        working_messages: list[ChatMessage],
        response: LLMResponse,
        tool_calls: list[LLMToolCall],
        tool_executor: CompositeToolExecutor,
    ) -> tuple[list[ChatMessage], bool]:
        """执行模型请求的工具调用，并把结果写回工作上下文。"""
        assistant_message = ChatMessage(
            role="assistant",
            text=response.content,
            reasoning_content=self._build_memory_reasoning_content(
                response.reasoning_content
            ),
            tool_calls=tool_calls,
        )
        working_messages.append(assistant_message)
        history_messages: list[ChatMessage] = [assistant_message]
        has_error = False
        for tool_call in tool_calls:
            tool_message, is_error = await self._call_tool_for_model(
                tool_call=tool_call,
                tool_executor=tool_executor,
            )
            working_messages.append(tool_message)
            history_messages.append(tool_message)
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

    def _append_correction(
        self,
        *,
        working_messages: list[ChatMessage],
        corrections_count: int,
        reason: str,
    ) -> int:
        """向模型追加工具流程纠错提示。"""
        next_count = corrections_count + 1
        if next_count > self.config.correction_retry_count:
            raise RuntimeError(f"AI 群聊工具调用纠错超过最大次数: {reason}")
        log_event(
            level="WARNING",
            event="ai_group_chat.tool_flow_correction",
            category="plugin",
            message="AI 群聊工具调用需要纠正",
            reason=reason,
            correction_count=next_count,
        )
        working_messages.append(
            ChatMessage(
                role="user",
                text=(
                    f"工具调用流程错误: {reason}\n"
                    "请严格遵守相关工具 description 与参数 schema，重新生成本轮回复。"
                ),
            )
        )
        return next_count

    def _persist_turn(
        self,
        *,
        chat_handler: ContextHandler,
        turn_messages: list[ChatMessage],
        tool_history_messages: list[ChatMessage],
        assistant_content: str | None,
        assistant_reasoning_content: str | None,
    ) -> None:
        """把本轮用户输入和最终回复写入群上下文。"""
        history_messages = turn_messages[:]
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
