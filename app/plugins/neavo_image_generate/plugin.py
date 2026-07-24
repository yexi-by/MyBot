"""仅面向配置群聊的 Neavo 图像双向插件。"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Literal, override

import aiofiles
import httpx

from app.models import (
    At,
    GroupMessage,
    Image,
    NapCatId,
    Reply,
    Text,
)
from app.models.segments import ImageData
from app.plugins.base import BasePlugin
from app.utils.file_type import detect_mime_type
from app.utils.log import log_event

from .client import (
    MAX_INPUT_IMAGE_BYTES,
    MAX_INSTRUCTION_LENGTH,
    SUPPORTED_INPUT_MIME_TYPES,
    NeavoGenerationTimeoutError,
    NeavoImageClient,
    NeavoImageError,
)
from .config import NeavoImageGenerateConfig, load_neavo_image_generate_config

COMMAND_TOKEN: Final[str] = "#生图"
REVERSE_COMMAND_TOKEN: Final[str] = "#反推"
MAX_PROMPT_LENGTH: Final[int] = MAX_INSTRUCTION_LENGTH
CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 100

type NeavoOperation = Literal["text_to_image", "image_to_text"]


@dataclass(frozen=True, slots=True)
class NeavoCommand:
    """一条已识别的群聊命令。"""

    operation: NeavoOperation
    prompt: str | None = None


@dataclass(frozen=True, slots=True)
class LoadedInputImage:
    """一张已读取并校验的反推输入图片。"""

    image_bytes: bytes
    mime_type: str
    source: Literal["path", "url", "napcat_base64", "napcat_path", "napcat_url"]


class NeavoInputImageError(RuntimeError):
    """读取或校验反推输入图片失败。"""


def _extract_plain_text(msg: GroupMessage) -> str:
    """拼接消息中的全部文本段。"""
    return "".join(
        segment.data.text for segment in msg.message if isinstance(segment, Text)
    ).strip()


def extract_prompt(msg: GroupMessage) -> str | None:
    """提取独立 ``#生图`` 令牌后的提示词。"""
    text = _extract_plain_text(msg)
    if text == COMMAND_TOKEN:
        return ""
    if not text.startswith(COMMAND_TOKEN):
        return None
    remainder = text[len(COMMAND_TOKEN) :]
    if not remainder or not remainder[0].isspace():
        return None
    return remainder.strip()


def extract_command(msg: GroupMessage) -> NeavoCommand | None:
    """识别文生图或图片反推命令，近似文本不会触发。"""
    prompt = extract_prompt(msg)
    if prompt is not None:
        return NeavoCommand(operation="text_to_image", prompt=prompt)
    if _extract_plain_text(msg) == REVERSE_COMMAND_TOKEN:
        return NeavoCommand(operation="image_to_text")
    return None


class NeavoImageGeneratePlugin(BasePlugin[GroupMessage]):
    """响应白名单群聊中的 Neavo 文生图和图片反推指令。"""

    name: ClassVar[str] = "neavo群聊生图插件"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """读取配置并使用共享直连 HTTP 客户端初始化协议客户端。"""
        self.config: NeavoImageGenerateConfig = load_neavo_image_generate_config()
        self.group_ids: set[NapCatId] = set(self.config.group_ids)
        self.client: NeavoImageClient = NeavoImageClient(
            config=self.config,
            http_client=self.context.direct_httpx,
        )

    @override
    async def add_to_queue(self, msg: GroupMessage) -> bool:
        """在耗时队列外过滤无关群聊和普通消息。"""
        if msg.group_id not in self.group_ids or extract_command(msg) is None:
            return False
        return await super().add_to_queue(msg)

    @override
    async def run(self, msg: GroupMessage) -> bool:
        """执行命中的图像命令，并阻止消息继续进入低优先级插件。"""
        if msg.group_id not in self.group_ids:
            return False
        command = extract_command(msg)
        if command is None:
            return False
        if command.operation == "text_to_image":
            return await self._run_text_to_image(msg=msg, prompt=command.prompt or "")
        return await self._run_image_to_text(msg=msg)

    async def _run_text_to_image(self, *, msg: GroupMessage, prompt: str) -> bool:
        """校验文生图命令、等待任务并发送图片。"""
        if prompt == "":
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 请在 #生图 后填写图片描述。",
            )
            return True
        if len(prompt) > MAX_PROMPT_LENGTH:
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=f" 图片描述不能超过 {MAX_PROMPT_LENGTH} 个字符。",
            )
            return True

        self._log_request_accepted(msg=msg, operation="text_to_image")
        await self._send_text(
            group_id=msg.group_id,
            user_id=msg.user_id,
            text=" 已收到，正在生成图片…",
        )
        try:
            result = await self.client.generate(prompt=prompt)
        except NeavoGenerationTimeoutError as exc:
            self._log_expected_failure(
                msg=msg,
                exc=exc,
                operation="text_to_image",
            )
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 生图等待超时，请稍后再试。",
            )
            return True
        except NeavoImageError as exc:
            self._log_expected_failure(
                msg=msg,
                exc=exc,
                operation="text_to_image",
            )
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 生图失败，请稍后再试。",
            )
            return True

        encoded_image = base64.b64encode(result.image_bytes).decode("ascii")
        _ = await self.context.bot.send_msg(
            group_id=msg.group_id,
            message_segment=[
                At.new(msg.user_id),
                Image.new(f"base64://{encoded_image}"),
            ],
        )
        self._log_result_sent(
            msg=msg,
            operation="text_to_image",
            task_id=str(result.job_id),
        )
        return True

    async def _run_image_to_text(self, *, msg: GroupMessage) -> bool:
        """从当前消息或被回复消息读取图片并返回反推文本。"""
        image_segment = await self._find_reverse_image(msg=msg)
        if image_segment is None:
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 请携带一张图片，或回复一条含图片的消息后发送 #反推。",
            )
            return True
        try:
            input_image = await self._load_input_image(
                segment=image_segment,
                message_id=msg.message_id,
            )
        except NeavoInputImageError as exc:
            log_event(
                level="WARNING",
                event="neavo_image_generate.input.failed",
                category="plugin",
                message="Neavo 反推输入图片读取失败",
                operation="image_to_text",
                resource_type="image",
                group_id=msg.group_id,
                user_id=msg.user_id,
                message_id=msg.message_id,
                has_path=bool(image_segment.data.path),
                has_url=bool(image_segment.data.url),
                error_type=type(exc).__name__,
                failure_reason=str(exc),
            )
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 读取图片失败，请重新发送 JPEG、PNG 或 WebP 图片。",
            )
            return True

        self._log_request_accepted(msg=msg, operation="image_to_text")
        await self._send_text(
            group_id=msg.group_id,
            user_id=msg.user_id,
            text=" 已收到，正在反推图片描述…",
        )
        try:
            result = await self.client.describe(
                image_bytes=input_image.image_bytes,
                mime_type=input_image.mime_type,
            )
        except NeavoGenerationTimeoutError as exc:
            self._log_expected_failure(
                msg=msg,
                exc=exc,
                operation="image_to_text",
            )
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 反推等待超时，请稍后再试。",
            )
            return True
        except NeavoImageError as exc:
            self._log_expected_failure(
                msg=msg,
                exc=exc,
                operation="image_to_text",
            )
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 反推失败，请稍后再试。",
            )
            return True

        await self._send_text(
            group_id=msg.group_id,
            user_id=msg.user_id,
            text=f"\n{result.text}",
        )
        self._log_result_sent(
            msg=msg,
            operation="image_to_text",
            task_id=str(result.job_id),
        )
        return True

    async def _find_reverse_image(self, *, msg: GroupMessage) -> Image | None:
        """优先返回当前消息图片，否则读取被回复消息中的首张图片。"""
        current_image = self._first_image(msg=msg)
        if current_image is not None:
            return current_image
        reply_id = self._extract_reply_id(msg=msg)
        if reply_id is None:
            return None
        stored_message = await self.context.database.search_messages(
            self_id=msg.self_id,
            group_id=msg.group_id,
            message_id=reply_id,
        )
        if not isinstance(stored_message, GroupMessage):
            return None
        return self._first_image(msg=stored_message)

    @staticmethod
    def _first_image(*, msg: GroupMessage) -> Image | None:
        """返回单条消息中的首张图片。"""
        return next(
            (segment for segment in msg.message if isinstance(segment, Image)),
            None,
        )

    @staticmethod
    def _extract_reply_id(*, msg: GroupMessage) -> NapCatId | None:
        """提取当前消息引用的消息 ID。"""
        for segment in msg.message:
            if isinstance(segment, Reply):
                return segment.data.id
        return None

    async def _load_input_image(
        self,
        *,
        segment: Image,
        message_id: NapCatId,
    ) -> LoadedInputImage:
        """按本地路径、原始 URL、NapCat 刷新的顺序读取输入图片。"""
        data = segment.data
        loaded: LoadedInputImage | None = None
        if data.path:
            path = Path(data.path)
            if path.is_file():
                image_bytes = await self._read_bounded_path(path=path)
                loaded = self._validate_loaded_image(
                    image_bytes=image_bytes,
                    source="path",
                )
        if loaded is None and data.url:
            try:
                image_bytes = await self._download_bounded_url(url=data.url)
            except NeavoInputImageError:
                log_event(
                    level="WARNING",
                    event="neavo_image_generate.input.url_failed",
                    category="plugin",
                    message="Neavo 反推图片原始 URL 下载失败，准备刷新资源",
                    operation="image_to_text",
                    resource_type="image",
                    message_id=message_id,
                    has_path=bool(data.path),
                    has_url=True,
                    failure_reason="direct_url_unavailable",
                )
            else:
                loaded = self._validate_loaded_image(
                    image_bytes=image_bytes,
                    source="url",
                )
        if loaded is None:
            loaded = await self._load_refreshed_image(data=data)

        log_event(
            level="INFO",
            event="neavo_image_generate.input.loaded",
            category="plugin",
            message="Neavo 反推输入图片读取完成",
            operation="image_to_text",
            resource_type="image",
            message_id=message_id,
            has_path=bool(data.path),
            has_url=bool(data.url),
            source=loaded.source,
            image_bytes=len(loaded.image_bytes),
            mime_type=loaded.mime_type,
        )
        return loaded

    async def _load_refreshed_image(self, *, data: ImageData) -> LoadedInputImage:
        """调用 NapCat 刷新图片信息，并读取返回的本地或网络资源。"""
        if not data.file and not data.file_id:
            raise NeavoInputImageError("图片没有可用于刷新的文件标识")
        response = await self.context.bot.get_image(
            file=data.file or None,
            file_id=data.file_id,
        )
        if response.status != "ok" or response.retcode != 0:
            raise NeavoInputImageError("NapCat 刷新图片资源失败")
        response_data = response.data if isinstance(response.data, dict) else {}

        raw_base64 = response_data.get("base64")
        if isinstance(raw_base64, str) and raw_base64.strip():
            try:
                image_bytes = base64.b64decode(raw_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise NeavoInputImageError("NapCat 返回了无效图片 Base64") from exc
            return self._validate_loaded_image(
                image_bytes=image_bytes,
                source="napcat_base64",
            )

        for key in ("path", "file"):
            raw_path = response_data.get(key)
            if not isinstance(raw_path, str):
                continue
            path = Path(raw_path)
            if path.is_file():
                image_bytes = await self._read_bounded_path(path=path)
                return self._validate_loaded_image(
                    image_bytes=image_bytes,
                    source="napcat_path",
                )

        raw_url = response_data.get("url")
        if isinstance(raw_url, str) and raw_url:
            image_bytes = await self._download_bounded_url(url=raw_url)
            return self._validate_loaded_image(
                image_bytes=image_bytes,
                source="napcat_url",
            )
        raise NeavoInputImageError("NapCat 刷新结果没有可读取的图片内容")

    async def _read_bounded_path(self, *, path: Path) -> bytes:
        """从本地路径读取不超过 10 MiB 的图片。"""
        try:
            if path.stat().st_size > MAX_INPUT_IMAGE_BYTES:
                raise NeavoInputImageError("图片超过 10 MiB")
            async with aiofiles.open(path, mode="rb") as file:
                image_bytes = await file.read(MAX_INPUT_IMAGE_BYTES + 1)
        except NeavoInputImageError:
            raise
        except OSError as exc:
            raise NeavoInputImageError("读取本地图片失败") from exc
        if len(image_bytes) > MAX_INPUT_IMAGE_BYTES:
            raise NeavoInputImageError("图片超过 10 MiB")
        return image_bytes

    async def _download_bounded_url(self, *, url: str) -> bytes:
        """从 URL 流式下载不超过 10 MiB 的图片。"""
        try:
            async with self.context.direct_httpx.stream(
                "GET",
                url,
                timeout=self.config.request_timeout_seconds,
            ) as response:
                if response.status_code != 200:
                    raise NeavoInputImageError(
                        f"图片 URL 返回 HTTP {response.status_code}"
                    )
                content_length = self._parse_content_length(response=response)
                if (
                    content_length is not None
                    and content_length > MAX_INPUT_IMAGE_BYTES
                ):
                    raise NeavoInputImageError("图片超过 10 MiB")
                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > MAX_INPUT_IMAGE_BYTES:
                        raise NeavoInputImageError("图片超过 10 MiB")
        except NeavoInputImageError:
            raise
        except httpx.RequestError as exc:
            raise NeavoInputImageError("图片 URL 下载发生网络错误") from exc
        return bytes(buffer)

    @staticmethod
    def _validate_loaded_image(
        *,
        image_bytes: bytes,
        source: Literal[
            "path", "url", "napcat_base64", "napcat_path", "napcat_url"
        ],
    ) -> LoadedInputImage:
        """根据文件签名校验反推图片类型。"""
        if not image_bytes:
            raise NeavoInputImageError("图片内容为空")
        if len(image_bytes) > MAX_INPUT_IMAGE_BYTES:
            raise NeavoInputImageError("图片超过 10 MiB")
        try:
            mime_type = detect_mime_type(image_bytes)
        except ValueError as exc:
            raise NeavoInputImageError("无法识别图片格式") from exc
        if mime_type not in SUPPORTED_INPUT_MIME_TYPES:
            raise NeavoInputImageError("反推只支持 JPEG、PNG 或 WebP")
        return LoadedInputImage(
            image_bytes=image_bytes,
            mime_type=mime_type,
            source=source,
        )

    @staticmethod
    def _parse_content_length(*, response: httpx.Response) -> int | None:
        """解析响应中的可选非负 Content-Length。"""
        raw_content_length = response.headers.get("content-length")
        if raw_content_length is None:
            return None
        try:
            content_length = int(raw_content_length)
        except ValueError:
            return None
        return content_length if content_length >= 0 else None

    async def _send_text(
        self,
        *,
        group_id: NapCatId,
        user_id: NapCatId,
        text: str,
    ) -> None:
        """按艾特、文本的固定顺序发送群内状态。"""
        _ = await self.context.bot.send_msg(
            group_id=group_id,
            message_segment=[At.new(user_id), Text.new(text)],
        )

    @staticmethod
    def _log_request_accepted(
        *,
        msg: GroupMessage,
        operation: NeavoOperation,
    ) -> None:
        """记录不含提示词和密钥的任务接收日志。"""
        log_event(
            level="INFO",
            event="neavo_image_generate.request.accepted",
            category="plugin",
            message="Neavo 图像任务已接收",
            operation=operation,
            stage="accepted",
            group_id=msg.group_id,
            user_id=msg.user_id,
        )

    @staticmethod
    def _log_result_sent(
        *,
        msg: GroupMessage,
        operation: NeavoOperation,
        task_id: str,
    ) -> None:
        """记录任务结果已发送。"""
        log_event(
            level="SUCCESS",
            event="neavo_image_generate.result.sent",
            category="plugin",
            message="Neavo 图像任务结果已发送",
            operation=operation,
            stage="result",
            status_code=200,
            task_id=task_id,
            group_id=msg.group_id,
            user_id=msg.user_id,
        )

    @staticmethod
    def _log_expected_failure(
        *,
        msg: GroupMessage,
        exc: NeavoImageError,
        operation: NeavoOperation,
    ) -> None:
        """仅记录安全的关联字段，不回显上游正文、地址或密钥。"""
        log_event(
            level="WARNING",
            event="neavo_image_generate.request.failed",
            category="plugin",
            message="Neavo 图像任务失败",
            operation=operation,
            stage=exc.stage,
            status_code=exc.status_code,
            task_id=str(exc.job_id) if exc.job_id is not None else None,
            group_id=msg.group_id,
            user_id=msg.user_id,
        )


__all__ = [
    "COMMAND_TOKEN",
    "CONSUMERS_COUNT",
    "MAX_PROMPT_LENGTH",
    "NeavoCommand",
    "NeavoImageGeneratePlugin",
    "PRIORITY",
    "REVERSE_COMMAND_TOKEN",
    "extract_command",
    "extract_prompt",
]
