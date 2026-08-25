"""NapCat 群聊工具执行器。"""

from collections.abc import Callable
from typing import override

import httpx

from app.database import GroupMessageReader
from app.models import (
    GroupMessage,
    JsonObject,
    JsonValue,
    MessageSegment,
    Response,
)
from app.services.llm.schemas import LLMToolDefinition, LLMToolExecutor
from app.services.llm.tools import LLMToolExecutionResult, LLMToolRegistry
from app.services.napcat.message_formatter import NapCatMessageTextFormatter

from .files import GroupFileToolset
from .forward import GroupForwardToolset
from .forward_images import GroupForwardImageToolset
from .history import GroupHistoryToolset
from .modifiers import GroupMessageDirectiveParser
from .protocols import NapCatGroupToolBot


class NapCatGroupToolExecutor(LLMToolExecutor):
    """聚合当前群可用的 NapCat 本地工具。"""

    def __init__(
        self,
        bot: NapCatGroupToolBot,
        group_messages: GroupMessageReader,
        event: GroupMessage,
        allow_mention_all: bool,
        forward_image_tool_enabled: bool,
        forward_image_max_images_per_call: int,
        forward_image_max_images_per_turn: int,
        image_fetch_concurrency: int,
        image_download_timeout_seconds: float,
        image_max_bytes: int | None,
        forward_reply_threshold_chars: int,
        http_client: httpx.AsyncClient | None,
        message_formatter: NapCatMessageTextFormatter,
        history_default_limit: int,
        history_max_per_call: int,
        history_default_before_count: int,
        history_default_after_count: int,
        file_default_count: int,
        file_max_per_call: int,
        remaining_image_delivery_slots: Callable[[], int | None],
    ) -> None:
        """绑定当前群事件，并注册可供模型调用的群聊工具。"""
        self._registry: LLMToolRegistry = LLMToolRegistry()
        self._forward_image_tool_enabled: bool = forward_image_tool_enabled
        self._directives: GroupMessageDirectiveParser = GroupMessageDirectiveParser(
            bot=bot,
            event=event,
            allow_mention_all=allow_mention_all,
            forward_reply_threshold_chars=forward_reply_threshold_chars,
        )
        self._files: GroupFileToolset = GroupFileToolset(
            bot=bot,
            event=event,
            default_count=file_default_count,
            max_per_call=file_max_per_call,
        )
        self._forward: GroupForwardToolset = GroupForwardToolset(
            bot=bot,
            group_messages=group_messages,
            event=event,
            message_formatter=message_formatter,
        )
        self._forward_images: GroupForwardImageToolset = GroupForwardImageToolset(
            bot=bot,
            group_messages=group_messages,
            event=event,
            max_images_per_call=forward_image_max_images_per_call,
            max_images_per_turn=forward_image_max_images_per_turn,
            fetch_concurrency=image_fetch_concurrency,
            download_timeout_seconds=image_download_timeout_seconds,
            http_client=http_client,
            max_image_bytes=image_max_bytes,
            remaining_delivery_slots=remaining_image_delivery_slots,
        )
        self._history: GroupHistoryToolset = GroupHistoryToolset(
            group_messages=group_messages,
            event=event,
            message_formatter=message_formatter,
            default_limit=history_default_limit,
            max_per_call=history_max_per_call,
            default_before_count=history_default_before_count,
            default_after_count=history_default_after_count,
        )
        self._register_tools()

    def begin_image_delivery_batch(self) -> None:
        """通知图片工具开始处理同一条模型响应中的一批调用。"""
        self._forward_images.begin_delivery_batch()

    @override
    def list_tools(self) -> list[LLMToolDefinition]:
        """返回当前群聊可用工具。"""
        return self._registry.list_tools()

    @override
    async def call_tool(self, name: str, arguments: JsonObject) -> JsonValue:
        """调用当前群聊工具。"""
        return await self._registry.call_tool(name=name, arguments=arguments)

    async def call_tool_with_artifacts(
        self, name: str, arguments: JsonObject
    ) -> LLMToolExecutionResult:
        """调用当前群聊工具，并保留内部图片附件。"""
        return await self._registry.call_tool_with_artifacts(
            name=name,
            arguments=arguments,
        )

    def build_message_segments_from_content(self, content: str) -> list[MessageSegment]:
        """把模型 content 标记转换为 NapCat 消息段。"""
        return self._directives.build_message_segments(content=content)

    async def send_content(self, content: str) -> Response:
        """发送模型 content，并应用 `<Reply>` / `<At>` 标记。"""
        return await self._directives.send_content(content=content)

    def _register_tools(self) -> None:
        """注册当前群聊工具定义。"""
        self._files.register_tools(self._registry)
        self._forward.register_tools(self._registry)
        if self._forward_image_tool_enabled:
            self._forward_images.register_tools(self._registry)
        self._history.register_tools(self._registry)
