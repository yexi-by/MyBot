"""AI 群聊插件的 Markdown 调试上下文转储。"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

import aiofiles

from app.models import JsonValue, NapCatId, to_json_value
from app.services.llm.schemas import (
    ChatMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolChoice,
    LLMToolDefinition,
)

from .config import AIGroupChatConfig, GroupChatConfig
from .constants import BEIJING_TIMEZONE, DEBUG_DUMP_DIR

type DumpPhase = Literal[
    "启动初始化",
    "模型请求",
    "模型响应",
    "工具结果",
    "长期上下文",
]


class AIGroupChatDebugDumper:
    """按启动批次把每个群的 LLM messages 完整写入 Markdown 文件。"""

    def __init__(self, *, config: AIGroupChatConfig) -> None:
        """保存调试转储配置，并生成本次进程启动的文件名。"""
        self.enabled: bool = config.debug_dump_messages
        self.root_dir: Path = DEBUG_DUMP_DIR
        self.started_at: datetime = datetime.now(BEIJING_TIMEZONE)
        self.session_name: str = self.started_at.strftime("%Y%m%d_%H%M%S_%f")
        self._paths: dict[str, Path] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def initialize_group(
        self, *, group_config: GroupChatConfig, messages: list[ChatMessage]
    ) -> Path | None:
        """为单个群创建本次启动的 Markdown 调试文件。"""
        if not self.enabled:
            return None
        group_id = str(group_config.group_id)
        path = self._ensure_group_file(group_id=group_id)
        lines = [
            f"# AI 群聊上下文调试 - 群 {group_id}",
            "",
            f"- 启动时间: {self.started_at.strftime('%Y-%m-%d %H:%M:%S %z')}",
            f"- 群号: `{group_id}`",
            f"- 最大上下文条数: `{group_config.max_context_length}`",
            f"- 系统提示词文件: `{group_config.system_prompt_path}`",
            f"- 知识库文件: `{group_config.knowledge_base_path}`",
            "",
            *self._format_messages(
                title="启动初始化 messages",
                messages=messages,
            ),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    async def append_llm_request(
        self,
        *,
        group_id: NapCatId,
        round_index: int,
        messages: list[ChatMessage],
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice,
    ) -> None:
        """追加一次发送给模型的完整请求上下文。"""
        lines = [
            *self._format_section_header(phase="模型请求", round_index=round_index),
            f"- tool_choice: `{self._format_tool_choice(tool_choice=tool_choice)}`",
            f"- tools_count: `{len(tools)}`",
            "",
            *self._format_tools(tools=tools),
            "",
            *self._format_messages(title="发送给模型的 messages", messages=messages),
        ]
        await self._append_section(group_id=group_id, lines=lines)

    async def append_llm_response(
        self,
        *,
        group_id: NapCatId,
        round_index: int,
        response: LLMResponse,
        modifier_calls: list[LLMToolCall],
        information_calls: list[LLMToolCall],
    ) -> None:
        """追加一次模型响应的详细内容。"""
        lines = [
            *self._format_section_header(phase="模型响应", round_index=round_index),
            f"- content_length: `{self._text_length(response.content)}`",
            f"- reasoning_length: `{self._text_length(response.reasoning_content)}`",
            f"- tool_calls_count: `{len(response.tool_calls)}`",
            f"- modifier_tool_calls_count: `{len(modifier_calls)}`",
            f"- information_tool_calls_count: `{len(information_calls)}`",
            "",
            "### content",
            "",
            *self._format_optional_text(
                value=response.content,
                language="markdown",
                empty_text="（模型没有返回正文）",
            ),
            "",
            "### reasoning_content",
            "",
            *self._format_optional_text(
                value=response.reasoning_content,
                language="markdown",
                empty_text="（模型没有返回 reasoning_content）",
            ),
            "",
            *self._format_tool_calls(tool_calls=response.tool_calls),
        ]
        await self._append_section(group_id=group_id, lines=lines)

    async def append_tool_result(
        self,
        *,
        group_id: NapCatId,
        tool_call: LLMToolCall,
        tool_message: ChatMessage,
        is_error: bool,
    ) -> None:
        """追加一次工具执行结果。"""
        lines = [
            *self._format_section_header(phase="工具结果", round_index=None),
            f"- tool_call_id: `{tool_call.id}`",
            f"- tool_name: `{tool_call.name}`",
            f"- is_error: `{is_error}`",
            "",
            "### arguments",
            "",
            *self._json_block(value=tool_call.arguments),
            "",
            "### tool message",
            "",
            *self._format_messages(title="写回模型的 tool message", messages=[tool_message]),
        ]
        await self._append_section(group_id=group_id, lines=lines)

    async def append_context_snapshot(
        self,
        *,
        group_id: NapCatId,
        title: str,
        messages: list[ChatMessage],
    ) -> None:
        """追加当前群长期上下文快照。"""
        lines = [
            *self._format_section_header(phase="长期上下文", round_index=None),
            *self._format_messages(title=title, messages=messages),
        ]
        await self._append_section(group_id=group_id, lines=lines)

    def _ensure_group_file(self, *, group_id: str) -> Path:
        """确保指定群的本次启动调试文件存在。"""
        cached_path = self._paths.get(group_id)
        if cached_path is not None:
            return cached_path
        group_dir = self.root_dir / group_id
        group_dir.mkdir(parents=True, exist_ok=True)
        path = group_dir / f"{self.session_name}.md"
        self._paths[group_id] = path
        if not path.exists():
            path.write_text(
                "\n".join(
                    [
                        f"# AI 群聊上下文调试 - 群 {group_id}",
                        "",
                        f"- 启动时间: {self.started_at.strftime('%Y-%m-%d %H:%M:%S %z')}",
                        f"- 群号: `{group_id}`",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        return path

    async def _append_section(
        self, *, group_id: NapCatId, lines: list[str]
    ) -> None:
        """把一个 Markdown 段落追加到指定群的调试文件。"""
        if not self.enabled:
            return
        group_key = str(group_id)
        path = self._ensure_group_file(group_id=group_key)
        lock = self._lock_for_group(group_id=group_key)
        async with lock:
            async with aiofiles.open(path, mode="a", encoding="utf-8") as file:
                await file.write("\n\n" + "\n".join(lines) + "\n")

    def _lock_for_group(self, *, group_id: str) -> asyncio.Lock:
        """返回指定群调试文件的异步写锁。"""
        lock = self._locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[group_id] = lock
        return lock

    def _format_section_header(
        self, *, phase: DumpPhase, round_index: int | None
    ) -> list[str]:
        """格式化调试段落标题。"""
        now = datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %z")
        title = f"## {phase}"
        if round_index is not None:
            title = f"{title} #{round_index}"
        return [title, "", f"- 记录时间: {now}"]

    def _format_messages(
        self, *, title: str, messages: list[ChatMessage]
    ) -> list[str]:
        """格式化完整 ChatMessage 列表。"""
        lines = [f"## {title}", "", f"- messages_count: `{len(messages)}`"]
        for index, message in enumerate(messages, start=1):
            lines.extend(self._format_message(index=index, message=message))
        return lines

    def _format_message(self, *, index: int, message: ChatMessage) -> list[str]:
        """格式化单条 ChatMessage。"""
        lines = [
            "",
            f"### message {index}: `{message.role}`",
            "",
            f"- text_length: `{self._text_length(message.text)}`",
            f"- reasoning_length: `{self._text_length(message.reasoning_content)}`",
            f"- image_count: `{len(message.image or [])}`",
            f"- tool_calls_count: `{len(message.tool_calls or [])}`",
        ]
        if message.tool_call_id is not None:
            lines.append(f"- tool_call_id: `{message.tool_call_id}`")
        if message.image:
            image_lengths = [len(image_bytes) for image_bytes in message.image]
            lines.append(f"- image_bytes: `{image_lengths}`")
        lines.extend(["", "#### text", ""])
        lines.extend(
            self._format_optional_text(
                value=message.text,
                language="markdown",
                empty_text="（无 text）",
            )
        )
        if message.reasoning_content is not None:
            lines.extend(["", "#### reasoning_content", ""])
            lines.extend(
                self._format_optional_text(
                    value=message.reasoning_content,
                    language="markdown",
                    empty_text="（无 reasoning_content）",
                )
            )
        if message.tool_calls:
            lines.extend(["", "#### tool_calls", ""])
            lines.extend(self._format_tool_calls(tool_calls=message.tool_calls))
        return lines

    def _format_tools(self, *, tools: list[LLMToolDefinition]) -> list[str]:
        """格式化当前暴露给模型的工具定义。"""
        if not tools:
            return ["## tools", "", "（本轮没有可用工具）"]
        lines = ["## tools"]
        for index, tool in enumerate(tools, start=1):
            lines.extend(["", f"### tool {index}: `{tool.name}`", ""])
            lines.extend(self._json_block(value=to_json_value(tool)))
        return lines

    def _format_tool_calls(self, *, tool_calls: list[LLMToolCall]) -> list[str]:
        """格式化模型返回的工具调用。"""
        if not tool_calls:
            return ["### tool_calls", "", "（无工具调用）"]
        lines = ["### tool_calls"]
        for index, tool_call in enumerate(tool_calls, start=1):
            lines.extend(["", f"#### tool_call {index}: `{tool_call.name}`", ""])
            lines.extend(self._json_block(value=to_json_value(tool_call)))
        return lines

    def _format_optional_text(
        self, *, value: str | None, language: str, empty_text: str
    ) -> list[str]:
        """格式化可选文本，保留原始换行。"""
        if value is None or value == "":
            return [empty_text]
        return self._fenced_block(language=language, value=value)

    def _json_block(self, *, value: JsonValue) -> list[str]:
        """格式化 JSON 值为 Markdown 代码块。"""
        json_text = json.dumps(value, ensure_ascii=False, indent=2)
        return self._fenced_block(language="json", value=json_text)

    def _fenced_block(self, *, language: str, value: str) -> list[str]:
        """根据内容自动选择不会冲突的 Markdown 代码块围栏。"""
        fence = "```"
        while fence in value:
            fence += "`"
        return [f"{fence}{language}", value, fence]

    def _format_tool_choice(self, *, tool_choice: LLMToolChoice) -> str:
        """格式化 tool_choice 参数。"""
        if isinstance(tool_choice, str):
            return tool_choice
        return json.dumps(tool_choice, ensure_ascii=False)

    def _text_length(self, value: str | None) -> int:
        """返回可选文本长度。"""
        if value is None:
            return 0
        return len(value)
