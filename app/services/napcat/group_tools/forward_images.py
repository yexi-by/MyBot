"""NapCat 群聊合并转发图片信息工具。"""

import asyncio
import base64
import binascii
from dataclasses import dataclass
from pathlib import Path

import aiofiles
import httpx
from pydantic import TypeAdapter, ValidationError

from app.models import (
    Forward,
    GroupMessage,
    Image,
    JsonObject,
    JsonValue,
    MessageSegment,
    NapCatId,
    Response,
    to_json_value,
)
from app.services.llm.tools import (
    LLMToolExecutionResult,
    LLMToolImageArtifact,
    LLMToolRegistry,
)
from app.utils.log import log_event

from .arguments import GetForwardMessageImagesArgs
from .protocols import NapCatGroupToolBot


@dataclass(frozen=True)
class ForwardImageTarget:
    """描述合并转发中的一张图片。"""

    message_id: NapCatId
    message_index: int
    image_index: int
    file: str | None
    file_id: str | None
    path: str | None
    url: str | None
    summary: str | None


@dataclass(frozen=True)
class ForwardImageFetchResult:
    """描述单张图片读取结果。"""

    target: ForwardImageTarget
    image_bytes: bytes | None
    metadata: JsonObject
    error: JsonObject | None


class GroupForwardImageToolset:
    """把合并转发图片读取能力暴露为 LLM 信息工具。"""

    def __init__(
        self,
        *,
        bot: NapCatGroupToolBot,
        event: GroupMessage,
        max_images_per_call: int,
        max_all_images: int,
        fetch_concurrency: int,
        download_timeout_seconds: float,
        http_client: httpx.AsyncClient | None,
    ) -> None:
        """绑定当前群事件和读取配置。"""
        self.bot: NapCatGroupToolBot = bot
        self.event: GroupMessage = event
        self.max_images_per_call: int = max_images_per_call
        self.max_all_images: int = max_all_images
        self.fetch_concurrency: int = fetch_concurrency
        self.download_timeout_seconds: float = download_timeout_seconds
        self.http_client: httpx.AsyncClient | None = http_client
        self.segments_adapter: TypeAdapter[list[MessageSegment]] = TypeAdapter(
            list[MessageSegment]
        )

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记合并转发图片读取工具。"""
        registry.register_tool(
            name="qq__get_forward_message_images",
            description=(
                "信息工具：读取合并转发消息中的图片内容。"
                "当你需要查看当前群收到的合并转发聊天记录里的图片时，应主动调用此工具。"
                "支持 single 单张、message 某条消息内全部图片、all 整个合并转发图片；"
                "all 模式会按配置上限截断，避免一次读取过多图片。"
                "此工具只返回图片元信息和内部图片附件，不发送群消息。"
            ),
            parameters_model=GetForwardMessageImagesArgs,
            handler=self.get_forward_message_images,
        )

    async def get_forward_message_images(
        self, arguments: JsonObject
    ) -> LLMToolExecutionResult:
        """读取合并转发中的目标图片，并返回模型可见附件。"""
        args = GetForwardMessageImagesArgs.model_validate(arguments)
        root_messages = await self._load_root_forward_messages(message_id=args.message_id)
        if root_messages is None:
            return LLMToolExecutionResult(
                result=self._build_root_error_result(message_id=args.message_id)
            )
        all_targets = self._collect_image_targets(
            message_id=args.message_id,
            raw_messages=root_messages,
        )
        self._log_collected_targets(
            message_id=args.message_id,
            mode=args.mode,
            targets=all_targets,
        )
        selected_targets = self._select_targets(args=args, targets=all_targets)
        limited_targets, truncated = self._limit_targets(
            args=args,
            targets=selected_targets,
        )
        fetch_results = await self._fetch_targets(targets=limited_targets)
        self._log_fetch_results(
            message_id=args.message_id,
            mode=args.mode,
            results=fetch_results,
            truncated=truncated,
        )
        images = [result.metadata for result in fetch_results if result.error is None]
        errors: list[JsonObject] = []
        for result in fetch_results:
            if result.error is not None:
                errors.append(result.error)
        artifacts = [
            self._build_artifact(result=result)
            for result in fetch_results
            if result.error is None and result.image_bytes is not None
        ]
        return LLMToolExecutionResult(
            result={
                "ok": True,
                "action": "get_forward_message_images",
                "group_id": to_json_value(self.event.group_id),
                "message_id": args.message_id,
                "mode": args.mode,
                "total_images": len(selected_targets),
                "returned_count": len(images),
                "truncated": truncated,
                "images": to_json_value(images),
                "errors": to_json_value(errors),
                "message": self._build_result_message(
                    returned_count=len(images),
                    errors_count=len(errors),
                    truncated=truncated,
                ),
            },
            image_artifacts=artifacts,
        )

    async def _load_root_forward_messages(
        self, *, message_id: NapCatId
    ) -> list[JsonValue] | None:
        """调用 NapCat 读取合并转发根消息列表。"""
        response = await self.bot.get_forward_msg(message_id=message_id)
        if response.status != "ok" or response.retcode != 0:
            return None
        data = response.data
        if not isinstance(data, dict):
            return None
        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            return None
        return [to_json_value(message) for message in raw_messages]

    def _collect_image_targets(
        self, *, message_id: NapCatId, raw_messages: list[JsonValue]
    ) -> list[ForwardImageTarget]:
        """从合并转发消息列表中收集图片定位信息。"""
        targets: list[ForwardImageTarget] = []
        for message_index, raw_message in enumerate(raw_messages, start=1):
            segments = self._parse_segments(raw_message=raw_message)
            image_index = 0
            for segment in segments:
                if isinstance(segment, Image):
                    image_index += 1
                    targets.append(
                        ForwardImageTarget(
                            message_id=message_id,
                            message_index=message_index,
                            image_index=image_index,
                            file=segment.data.file,
                            file_id=segment.data.file_id,
                            path=segment.data.path,
                            url=segment.data.url,
                            summary=segment.data.summary,
                        )
                    )
                    continue
                if isinstance(segment, Forward) and segment.data.content is not None:
                    targets.extend(
                        self._collect_embedded_forward_targets(
                            parent_message_id=message_id,
                            parent_message_index=message_index,
                            segment=segment,
                        )
                    )
        return targets

    def _collect_embedded_forward_targets(
        self,
        *,
        parent_message_id: NapCatId,
        parent_message_index: int,
        segment: Forward,
    ) -> list[ForwardImageTarget]:
        """收集已内嵌在消息段里的合并转发图片。"""
        content = segment.data.content
        if not isinstance(content, list):
            return []
        nested_targets = self._collect_image_targets(
            message_id=segment.data.id or parent_message_id,
            raw_messages=[to_json_value(item) for item in content],
        )
        return [
            ForwardImageTarget(
                message_id=target.message_id,
                message_index=parent_message_index,
                image_index=target.image_index,
                file=target.file,
                file_id=target.file_id,
                path=target.path,
                url=target.url,
                summary=target.summary,
            )
            for target in nested_targets
        ]

    def _parse_segments(self, *, raw_message: JsonValue) -> list[MessageSegment]:
        """从合并转发条目中解析消息段。"""
        content = self._extract_message_content(raw_message=raw_message)
        if not isinstance(content, list):
            return []
        try:
            return self.segments_adapter.validate_python(content)
        except ValidationError:
            return []

    def _extract_message_content(self, *, raw_message: JsonValue) -> JsonValue:
        """提取合并转发条目的 message/content 字段。"""
        if not isinstance(raw_message, dict):
            return raw_message
        for key in ("message", "content", "messages"):
            value = raw_message.get(key)
            if value is not None:
                return value
        data = raw_message.get("data")
        if isinstance(data, dict):
            for key in ("message", "content", "messages"):
                value = data.get(key)
                if value is not None:
                    return value
        return None

    def _select_targets(
        self, *, args: GetForwardMessageImagesArgs, targets: list[ForwardImageTarget]
    ) -> list[ForwardImageTarget]:
        """按工具参数筛选目标图片。"""
        if args.mode == "all":
            return targets
        if args.message_index is None:
            return []
        message_targets = [
            target for target in targets if target.message_index == args.message_index
        ]
        if args.mode == "message":
            return message_targets
        if args.image_index is None:
            return []
        return [
            target
            for target in message_targets
            if target.image_index == args.image_index
        ]

    def _limit_targets(
        self, *, args: GetForwardMessageImagesArgs, targets: list[ForwardImageTarget]
    ) -> tuple[list[ForwardImageTarget], bool]:
        """按模式和配置限制本次读取图片数量。"""
        configured_limit = (
            self.max_all_images if args.mode == "all" else self.max_images_per_call
        )
        if args.max_images is None:
            limit = configured_limit
        else:
            limit = min(args.max_images, configured_limit)
        return targets[:limit], len(targets) > limit

    async def _fetch_targets(
        self, *, targets: list[ForwardImageTarget]
    ) -> list[ForwardImageFetchResult]:
        """并发读取目标图片。"""
        semaphore = asyncio.Semaphore(self.fetch_concurrency)

        async def fetch_one(target: ForwardImageTarget) -> ForwardImageFetchResult:
            async with semaphore:
                return await self._fetch_target(target=target)

        return await asyncio.gather(*(fetch_one(target) for target in targets))

    async def _fetch_target(
        self, *, target: ForwardImageTarget
    ) -> ForwardImageFetchResult:
        """读取单张图片并整理为附件和元信息。"""
        metadata = self._build_image_metadata(target=target, source=None)
        direct_error: JsonObject | None = None
        try:
            direct_image = await self._load_direct_image_bytes(target=target)
        except Exception as exc:
            direct_error = self._build_image_error(
                target=target,
                error_type=type(exc).__name__,
                error=f"直接读取图片失败: {exc}",
            )
        else:
            if direct_image is not None:
                image_bytes, source = direct_image
                return ForwardImageFetchResult(
                    target=target,
                    image_bytes=image_bytes,
                    metadata=self._build_image_metadata(
                        target=target,
                        source=source,
                    ),
                    error=None,
                )
        if target.file is None and target.file_id is None:
            return ForwardImageFetchResult(
                target=target,
                image_bytes=None,
                metadata=metadata,
                error=direct_error
                or self._build_image_error(
                    target=target,
                    error_type="MissingImageIdentifier",
                    error="图片段缺少 file 和 file_id",
                ),
            )
        try:
            response = await self._refresh_image_info(target=target)
        except Exception as exc:
            return ForwardImageFetchResult(
                target=target,
                image_bytes=None,
                metadata=metadata,
                error=self._build_image_error(
                    target=target,
                    error_type=type(exc).__name__,
                    error=f"NapCat 刷新图片信息失败: {exc}",
                ),
            )
        if response.status != "ok" or response.retcode != 0:
            return ForwardImageFetchResult(
                target=target,
                image_bytes=None,
                metadata=metadata,
                error=self._build_image_error(
                    target=target,
                    error_type="NapCatActionFailed",
                    error=response.message or response.wording or "NapCat 返回失败",
                ),
            )
        try:
            image_bytes = await self._load_image_bytes(response=response, target=target)
        except Exception as exc:
            return ForwardImageFetchResult(
                target=target,
                image_bytes=None,
                metadata=metadata,
                error=self._build_image_error(
                    target=target,
                    error_type=type(exc).__name__,
                    error=f"读取 NapCat 图片响应失败: {exc}",
                ),
            )
        if image_bytes is None:
            return ForwardImageFetchResult(
                target=target,
                image_bytes=None,
                metadata=metadata,
                error=self._build_image_error(
                    target=target,
                    error_type="ImageContentUnavailable",
                    error="NapCat 返回的图片信息没有可读取内容",
                ),
            )
        return ForwardImageFetchResult(
            target=target,
            image_bytes=image_bytes,
            metadata=self._build_image_metadata(
                target=target,
                source="napcat_refresh",
            ),
            error=None,
        )

    async def _load_direct_image_bytes(
        self, *, target: ForwardImageTarget
    ) -> tuple[bytes, str] | None:
        """优先从合并转发图片段自身携带的本地路径或 URL 读取图片。"""
        if target.path is not None:
            path = Path(target.path)
            if path.is_file():
                async with aiofiles.open(path, mode="rb") as file:
                    return await file.read(), "direct_path"
        if target.url is not None and self.http_client is not None:
            return await self._download_url(url=target.url), "direct_url"
        return None

    async def _refresh_image_info(self, *, target: ForwardImageTarget) -> Response:
        """通过 NapCat 刷新图片信息，作为直接下载不可用时的后备路径。"""
        if target.file is not None:
            return await self.bot.get_image(file=target.file)
        return await self.bot.get_image(file_id=target.file_id)

    async def _load_image_bytes(
        self, *, response: Response, target: ForwardImageTarget
    ) -> bytes | None:
        """从 NapCat 图片响应或原始图片段中读取图片字节。"""
        data = response.data if isinstance(response.data, dict) else {}
        raw_base64 = data.get("base64")
        if isinstance(raw_base64, str) and raw_base64.strip():
            try:
                return base64.b64decode(raw_base64, validate=True)
            except binascii.Error:
                return None
        for key in ("path", "file"):
            value = data.get(key)
            if isinstance(value, str):
                path = Path(value)
                if path.is_file():
                    async with aiofiles.open(path, mode="rb") as file:
                        return await file.read()
        url = data.get("url")
        if not isinstance(url, str):
            url = target.url
        if isinstance(url, str) and self.http_client is not None:
            return await self._download_url(url=url)
        return None

    async def _download_url(self, *, url: str) -> bytes:
        """通过本地 HTTP 客户端下载图片字节。"""
        if self.http_client is None:
            raise RuntimeError("合并转发图片 URL 下载需要配置 HTTP 客户端")
        response = await self.http_client.get(
            url,
            timeout=self.download_timeout_seconds,
        )
        response.raise_for_status()
        return response.content

    def _build_artifact(self, *, result: ForwardImageFetchResult) -> LLMToolImageArtifact:
        """把成功读取的图片整理为模型内部附件。"""
        if result.image_bytes is None:
            raise ValueError("构造图片附件时 image_bytes 不能为空")
        target = result.target
        label = f"合并转发第 {target.message_index} 条消息第 {target.image_index} 张图片"
        return LLMToolImageArtifact(
            label=label,
            image_bytes=result.image_bytes,
            metadata=result.metadata,
        )

    def _build_image_metadata(
        self, *, target: ForwardImageTarget, source: str | None
    ) -> JsonObject:
        """生成模型可读的图片元信息。"""
        return {
            "message_id": to_json_value(target.message_id),
            "message_index": target.message_index,
            "image_index": target.image_index,
            "file": target.file,
            "file_id": target.file_id,
            "path": target.path,
            "url": target.url,
            "summary": target.summary,
            "source": source,
        }

    def _log_collected_targets(
        self,
        *,
        message_id: NapCatId,
        mode: str,
        targets: list[ForwardImageTarget],
    ) -> None:
        """记录合并转发图片段字段，便于排查图片 URL 是否存在。"""
        log_event(
            level="DEBUG",
            event="napcat.group_tools.forward_images.targets_collected",
            category="napcat_tools",
            message="已收集合并转发图片段字段",
            group_id=to_json_value(self.event.group_id),
            message_id=to_json_value(message_id),
            mode=mode,
            image_count=len(targets),
            images=[
                {
                    "message_id": to_json_value(target.message_id),
                    "message_index": target.message_index,
                    "image_index": target.image_index,
                    "file": target.file,
                    "file_id": target.file_id,
                    "path": target.path,
                    "url": target.url,
                    "summary": target.summary,
                    "has_url": target.url is not None and target.url.strip() != "",
                }
                for target in targets
            ],
        )

    def _log_fetch_results(
        self,
        *,
        message_id: NapCatId,
        mode: str,
        results: list[ForwardImageFetchResult],
        truncated: bool,
    ) -> None:
        """记录合并转发图片读取结果，便于线上按来源定位问题。"""
        log_event(
            level="DEBUG",
            event="napcat.group_tools.forward_images.fetch_finished",
            category="napcat_tools",
            message="合并转发图片读取完成",
            group_id=to_json_value(self.event.group_id),
            message_id=to_json_value(message_id),
            mode=mode,
            requested_count=len(results),
            returned_count=sum(1 for result in results if result.error is None),
            errors_count=sum(1 for result in results if result.error is not None),
            truncated=truncated,
            images=[
                {
                    "message_id": to_json_value(result.target.message_id),
                    "message_index": result.target.message_index,
                    "image_index": result.target.image_index,
                    "file": result.target.file,
                    "file_id": result.target.file_id,
                    "path": result.target.path,
                    "url": result.target.url,
                    "summary": result.target.summary,
                    "source": result.metadata.get("source"),
                    "ok": result.error is None,
                    "error_type": (
                        result.error.get("error_type")
                        if result.error is not None
                        else None
                    ),
                    "error": (
                        result.error.get("error")
                        if result.error is not None
                        else None
                    ),
                }
                for result in results
            ],
        )

    def _build_image_error(
        self, *, target: ForwardImageTarget, error_type: str, error: str
    ) -> JsonObject:
        """生成单张图片读取失败信息。"""
        return {
            "message_id": to_json_value(target.message_id),
            "message_index": target.message_index,
            "image_index": target.image_index,
            "error_type": error_type,
            "error": error,
        }

    def _build_root_error_result(self, *, message_id: str) -> JsonObject:
        """构造根合并转发读取失败时的可恢复错误。"""
        return {
            "ok": False,
            "is_error": True,
            "action": "get_forward_message_images",
            "group_id": to_json_value(self.event.group_id),
            "message_id": message_id,
            "complete": False,
            "error_type": "NapCatActionFailed",
            "error": "合并转发消息读取失败",
            "message": "合并转发图片读取失败。请根据错误信息修正 message_id 或改用其他回复方式。",
            "images": [],
            "errors": [],
        }

    def _build_result_message(
        self, *, returned_count: int, errors_count: int, truncated: bool
    ) -> str:
        """生成工具结果给模型看的简短说明。"""
        parts = [f"已读取 {returned_count} 张合并转发图片。"]
        if errors_count > 0:
            parts.append(f"{errors_count} 张图片读取失败，详情见 errors。")
        if truncated:
            parts.append("结果已按配置上限截断，可缩小范围后再次读取。")
        return "".join(parts)
