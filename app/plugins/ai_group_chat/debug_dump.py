"""AI 群聊插件的长期上下文 Markdown 调试转储。"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import aiofiles

from app.models import NapCatId
from app.services.llm.schemas import ChatMessage, LLMToolCall
from app.utils.log import log_event

from .config import AIGroupChatConfig, GroupChatConfig
from .constants import BEIJING_TIMEZONE, DEBUG_DUMP_DIR

type DumpPhase = Literal["启动初始化", "长期上下文增量"]


@dataclass(frozen=True)
class MessageDelta:
    """描述一次 Markdown 转储需要追加的长期上下文增量。"""

    previous_count: int
    is_reset: bool
    messages: list[ChatMessage]


class AIGroupChatDebugDumper:
    """按启动批次把每个群的长期上下文增量写入 Markdown 文件。"""

    def __init__(self, *, config: AIGroupChatConfig) -> None:
        """保存调试转储配置，并生成本次进程启动的文件名。"""
        self.enabled: bool = config.debug_dump_messages
        self.root_dir: Path = DEBUG_DUMP_DIR
        self.started_at: datetime = datetime.now(BEIJING_TIMEZONE)
        self.session_name: str = self.started_at.strftime("%Y%m%d_%H%M%S_%f")
        self._paths: dict[str, Path] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_context_message_counts: dict[str, int] = {}
        self._context_section_counts: dict[str, int] = {}

    def initialize_group(
        self, *, group_config: GroupChatConfig, messages: list[ChatMessage]
    ) -> Path | None:
        """为单个群创建本次启动的 Markdown 调试文件。"""
        if not self.enabled:
            return None
        group_id = str(group_config.group_id)
        path = self._ensure_group_file(group_id=group_id)
        self._last_context_message_counts[group_id] = len(messages)
        lines = [
            f"# AI 群聊长期上下文调试 - 群 {group_id}",
            "",
            f"- 启动时间: {self.started_at.strftime('%Y-%m-%d %H:%M:%S %z')}",
            f"- 群号: `{group_id}`",
            f"- 最大上下文 token: `{group_config.max_context_tokens}`",
            f"- 系统提示词文件: `{group_config.system_prompt_path}`",
            f"- 知识库文件: `{group_config.knowledge_base_path}`",
            "- 记录策略: `只记录长期上下文 messages 增量，不记录完整 LLM 请求体`",
            "- 工具策略: `只记录工具名称摘要，不记录工具参数和工具结果正文`",
            "",
            *self._format_messages(
                title="启动初始化长期上下文",
                messages=messages,
                start_index=1,
                context_reset=False,
            ),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    async def append_context_snapshot(
        self,
        *,
        group_id: NapCatId,
        title: str,
        messages: list[ChatMessage],
    ) -> None:
        """追加当前群长期上下文增量。"""
        if not self.enabled:
            return
        group_key = str(group_id)
        path = self._ensure_group_file(group_id=group_key)
        lock = self._lock_for_group(group_id=group_key)
        async with lock:
            delta = self._consume_message_delta(
                group_id=group_key,
                messages=messages,
            )
            if not delta.messages:
                return
            section_index = self._next_context_section_index(group_id=group_key)
            start_index = 1 if delta.is_reset else delta.previous_count + 1
            section_title = f"长期上下文增量 #{section_index}"
            if delta.is_reset:
                section_title = f"长期上下文重建 #{section_index}"
            lines = [
                *self._format_section_header(
                    phase="长期上下文增量",
                    section_index=section_index,
                ),
                f"- title: `{title}`",
                f"- current_messages_count: `{len(messages)}`",
                f"- previous_messages_count: `{delta.previous_count}`",
                f"- new_messages_count: `{len(delta.messages)}`",
                f"- context_reset: `{delta.is_reset}`",
                "",
                *self._format_messages(
                    title=section_title,
                    messages=delta.messages,
                    start_index=start_index,
                    context_reset=delta.is_reset,
                ),
            ]
            try:
                await self._write_section(path=path, lines=lines)
            except OSError as exc:
                log_event(
                    level="WARNING",
                    event="ai_group_chat.debug_dump.write_failed",
                    category="plugin",
                    message="AI 群聊调试文件写入失败，已跳过本次调试转储",
                    group_id=group_key,
                    path=str(path),
                    error=str(exc),
                )

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
                        f"# AI 群聊长期上下文调试 - 群 {group_id}",
                        "",
                        f"- 启动时间: {self.started_at.strftime('%Y-%m-%d %H:%M:%S %z')}",
                        f"- 群号: `{group_id}`",
                        "- 记录策略: `只记录长期上下文 messages 增量，不记录完整 LLM 请求体`",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        return path

    async def _write_section(self, *, path: Path, lines: list[str]) -> None:
        """把已经格式化好的 Markdown 段落追加到文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        should_write_rebuild_header = not path.exists()
        async with aiofiles.open(path, mode="a", encoding="utf-8") as file:
            if should_write_rebuild_header:
                await file.write(
                    "\n".join(
                        [
                            f"# AI 群聊长期上下文调试 - 群 {path.parent.name}",
                            "",
                            "- 说明: `原调试文件或目录曾在运行中被删除，本文件已自动重建`",
                            "",
                        ]
                    )
                )
            await file.write("\n\n" + "\n".join(lines) + "\n")

    def _consume_message_delta(
        self,
        *,
        group_id: str,
        messages: list[ChatMessage],
    ) -> MessageDelta:
        """计算本次需要写入 Markdown 的长期上下文增量。"""
        previous_count = self._last_context_message_counts.get(group_id, 0)
        is_reset = len(messages) < previous_count
        if is_reset:
            delta_messages = messages
        else:
            delta_messages = messages[previous_count:]
        self._last_context_message_counts[group_id] = len(messages)
        return MessageDelta(
            previous_count=previous_count,
            is_reset=is_reset,
            messages=delta_messages,
        )

    def _next_context_section_index(self, *, group_id: str) -> int:
        """返回指定群长期上下文增量段落的递增编号。"""
        next_index = self._context_section_counts.get(group_id, 0) + 1
        self._context_section_counts[group_id] = next_index
        return next_index

    def _lock_for_group(self, *, group_id: str) -> asyncio.Lock:
        """返回指定群调试文件的异步写锁。"""
        lock = self._locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[group_id] = lock
        return lock

    def _format_section_header(
        self, *, phase: DumpPhase, section_index: int
    ) -> list[str]:
        """格式化调试段落标题。"""
        now = datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %z")
        return [f"## {phase} #{section_index}", "", f"- 记录时间: {now}"]

    def _format_messages(
        self,
        *,
        title: str,
        messages: list[ChatMessage],
        start_index: int,
        context_reset: bool,
    ) -> list[str]:
        """格式化长期上下文消息列表。"""
        lines = [
            f"### {title}",
            "",
            f"- messages_count: `{len(messages)}`",
            f"- start_index: `{start_index}`",
            f"- context_reset: `{context_reset}`",
        ]
        for offset, message in enumerate(messages):
            lines.extend(
                self._format_message(index=start_index + offset, message=message)
            )
        return lines

    def _format_message(self, *, index: int, message: ChatMessage) -> list[str]:
        """格式化单条长期上下文消息。"""
        lines = [
            "",
            f"#### message {index} / {message.role}",
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
        lines.extend(["", "##### text", ""])
        if message.role == "tool":
            lines.append("（tool message 内容已省略，避免工具结果撑爆调试文件）")
        else:
            lines.extend(
                self._format_optional_text(
                    value=message.text,
                    language="markdown",
                    empty_text="（无 text）",
                )
            )
        if message.reasoning_content is not None:
            lines.extend(["", "##### reasoning_content", ""])
            lines.extend(
                self._format_optional_text(
                    value=message.reasoning_content,
                    language="markdown",
                    empty_text="（无 reasoning_content）",
                )
            )
        if message.tool_calls:
            lines.extend(["", "##### 工具调用摘要", ""])
            lines.extend(self._format_tool_call_summary(tool_calls=message.tool_calls))
        return lines

    def _format_tool_call_summary(self, *, tool_calls: list[LLMToolCall]) -> list[str]:
        """只记录工具调用名称摘要，不记录参数内容。"""
        if not tool_calls:
            return ["（无工具调用）"]
        tool_names = ", ".join(f"`{tool_call.name}`" for tool_call in tool_calls)
        tool_ids = ", ".join(f"`{tool_call.id}`" for tool_call in tool_calls)
        return [
            f"- tool_calls_count: `{len(tool_calls)}`",
            f"- tool_names: {tool_names}",
            f"- tool_call_ids: {tool_ids}",
            "- note: 工具参数和工具结果已省略，避免调试文件膨胀。",
        ]

    def _format_optional_text(
        self, *, value: str | None, language: str, empty_text: str
    ) -> list[str]:
        """格式化可选文本，并保持原始换行。"""
        if value is None or value == "":
            return [empty_text]
        return self._fenced_block(language=language, value=value)

    def _fenced_block(self, *, language: str, value: str) -> list[str]:
        """根据内容自动选择不会冲突的 Markdown 代码块围栏。"""
        fence = "```"
        while fence in value:
            fence += "`"
        return [f"{fence}{language}", value, fence]

    def _text_length(self, value: str | None) -> int:
        """返回可选文本长度。"""
        if value is None:
            return 0
        return len(value)
