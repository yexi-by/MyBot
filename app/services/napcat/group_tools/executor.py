"""NapCat 群聊工具执行器。"""

from typing import override

import httpx

from app.models import (
    GroupMessage,
    JsonObject,
    JsonValue,
    MessageSegment,
    Response,
)
from app.services.llm.schemas import LLMToolDefinition, LLMToolExecutor
from app.services.llm.tools import LLMToolExecutionResult, LLMToolRegistry

from .files import GroupFileToolset
from .forward import GroupForwardToolset
from .forward_images import GroupForwardImageToolset
from .history import GroupHistoryToolset
from .modifiers import GroupMessageDirectiveParser
from .protocols import NapCatGroupHistoryDatabase, NapCatGroupToolBot


class NapCatGroupToolExecutor(LLMToolExecutor):
    """聚合当前群可用的 NapCat 本地工具。"""

    def __init__(
        self,
        bot: NapCatGroupToolBot,
        database: NapCatGroupHistoryDatabase,
        event: GroupMessage,
        allow_mention_all: bool = False,
        forward_image_tool_enabled: bool = True,
        forward_image_max_images_per_call: int = 6,
        forward_image_max_all_images: int = 12,
        forward_image_fetch_concurrency: int = 4,
        forward_image_download_timeout_seconds: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """绑定当前群事件，并注册可供模型调用的群聊工具。"""
        self._registry: LLMToolRegistry = LLMToolRegistry()
        self._forward_image_tool_enabled: bool = forward_image_tool_enabled
        self._directives: GroupMessageDirectiveParser = GroupMessageDirectiveParser(
            bot=bot,
            event=event,
            allow_mention_all=allow_mention_all,
        )
        self._files: GroupFileToolset = GroupFileToolset(bot=bot, event=event)
        self._forward: GroupForwardToolset = GroupForwardToolset(
            bot=bot,
            event=event,
        )
        self._forward_images: GroupForwardImageToolset = GroupForwardImageToolset(
            bot=bot,
            event=event,
            max_images_per_call=forward_image_max_images_per_call,
            max_all_images=forward_image_max_all_images,
            fetch_concurrency=forward_image_fetch_concurrency,
            download_timeout_seconds=forward_image_download_timeout_seconds,
            http_client=http_client,
        )
        self._history: GroupHistoryToolset = GroupHistoryToolset(
            database=database,
            event=event,
        )
        self._register_tools()

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
