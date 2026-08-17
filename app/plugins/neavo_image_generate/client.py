"""Neavo 图像双向 HTTP API 客户端。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

import httpx

from app.config import NeavoImageGenerateConfig
from app.utils.file_type import detect_mime_type

type NeavoRequestStage = Literal["submit", "poll", "validate"]
type SleepFunction = Callable[[float], Awaitable[None]]

MAX_CONSECUTIVE_POLL_RETRIES = 3
MAX_INSTRUCTION_LENGTH = 4096
MAX_INPUT_IMAGE_BYTES = 10 * 1024 * 1024
SUPPORTED_INPUT_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)


class NeavoImageError(RuntimeError):
    """Neavo 图像任务的可预期失败。"""

    def __init__(
        self,
        message: str,
        *,
        stage: NeavoRequestStage,
        status_code: int | None = None,
        job_id: UUID | None = None,
    ) -> None:
        """保存可安全用于日志关联的失败元数据。"""
        super().__init__(message)
        self.stage: NeavoRequestStage = stage
        self.status_code: int | None = status_code
        self.job_id: UUID | None = job_id


class NeavoTransportError(NeavoImageError):
    """访问 Neavo 服务时发生网络错误。"""


class NeavoProtocolError(NeavoImageError):
    """Neavo 响应或输入不符合约定协议。"""


class NeavoUpstreamError(NeavoImageError):
    """Neavo 服务返回终态错误状态。"""


class NeavoGenerationTimeoutError(NeavoImageError):
    """Neavo 图像任务超过总等待期限。"""


@dataclass(frozen=True, slots=True)
class NeavoImageResult:
    """一项完成的文生图任务。"""

    job_id: UUID
    image_bytes: bytes
    mime_type: str


@dataclass(frozen=True, slots=True)
class NeavoCaptionResult:
    """一项完成的图片反推任务。"""

    job_id: UUID
    text: str


class NeavoImageClient:
    """通过 Neavo 新版 API 执行文生图和图片反推任务。"""

    def __init__(
        self,
        *,
        config: NeavoImageGenerateConfig,
        http_client: httpx.AsyncClient,
        sleep: SleepFunction = asyncio.sleep,
    ) -> None:
        """绑定插件配置、共享直连 HTTP 客户端与可测试的休眠函数。"""
        self._config: NeavoImageGenerateConfig = config
        self._http_client: httpx.AsyncClient = http_client
        self._sleep: SleepFunction = sleep

    async def generate(self, prompt: str) -> NeavoImageResult:
        """通过 ``/text_to_image`` 提交指令并等待图片。"""
        job_id: UUID | None = None
        try:
            async with asyncio.timeout(self._config.generation_timeout_seconds):
                job_id = await self.submit_text_to_image(prompt=prompt)
                image_bytes, mime_type = await self.poll_text_to_image(job_id=job_id)
        except TimeoutError as exc:
            raise NeavoGenerationTimeoutError(
                "Neavo 文生图任务等待超时",
                stage="poll" if job_id is not None else "submit",
                job_id=job_id,
            ) from exc
        return NeavoImageResult(
            job_id=job_id,
            image_bytes=image_bytes,
            mime_type=mime_type,
        )

    async def describe(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
    ) -> NeavoCaptionResult:
        """通过 ``/image_to_text`` 提交图片并等待 Florence-2 原始文本。"""
        validated_mime_type = self._validate_input_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        job_id: UUID | None = None
        try:
            async with asyncio.timeout(self._config.generation_timeout_seconds):
                job_id = await self.submit_image_to_text(
                    image_bytes=image_bytes,
                    mime_type=validated_mime_type,
                )
                text = await self.poll_image_to_text(job_id=job_id)
        except TimeoutError as exc:
            raise NeavoGenerationTimeoutError(
                "Neavo 图片反推任务等待超时",
                stage="poll" if job_id is not None else "submit",
                job_id=job_id,
            ) from exc
        return NeavoCaptionResult(job_id=job_id, text=text)

    async def submit_text_to_image(self, *, prompt: str) -> UUID:
        """提交文生图任务；网络状态不明时不重试 POST。"""
        if not 1 <= len(prompt) <= MAX_INSTRUCTION_LENGTH:
            raise NeavoProtocolError(
                f"Neavo 生图指令长度必须为 1～{MAX_INSTRUCTION_LENGTH} 个字符",
                stage="submit",
            )
        return await self._submit_job(
            path="/text_to_image",
            json_body={"instruction": prompt},
        )

    async def submit_image_to_text(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
    ) -> UUID:
        """以原始图片请求体提交反推任务；网络状态不明时不重试 POST。"""
        return await self._submit_job(
            path="/image_to_text",
            content=image_bytes,
            content_type=mime_type,
        )

    async def _submit_job(
        self,
        *,
        path: str,
        json_body: dict[str, object] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> UUID:
        """提交一种任务并解析服务返回的 UUID。"""
        headers = self._authorization_headers()
        if content_type is not None:
            headers["Content-Type"] = content_type
        try:
            response = await self._http_client.post(
                f"{self._config.base_url}{path}",
                headers=headers,
                json=json_body,
                content=content,
                timeout=self._config.request_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise NeavoTransportError(
                "提交 Neavo 图像任务时网络请求失败",
                stage="submit",
            ) from exc

        if response.status_code != 202:
            raise NeavoUpstreamError(
                "Neavo 服务拒绝了图像任务",
                stage="submit",
                status_code=response.status_code,
            )
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise NeavoProtocolError(
                "Neavo 创建任务响应不是有效 JSON",
                stage="submit",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise NeavoProtocolError(
                "Neavo 创建任务响应必须是 JSON 对象",
                stage="submit",
                status_code=response.status_code,
            )
        response_object = cast(dict[str, object], payload)
        raw_job_id = response_object.get("id")
        if not isinstance(raw_job_id, str):
            raise NeavoProtocolError(
                "Neavo 创建任务响应缺少有效任务 ID",
                stage="submit",
                status_code=response.status_code,
            )
        try:
            return UUID(raw_job_id)
        except ValueError as exc:
            raise NeavoProtocolError(
                "Neavo 创建任务响应包含无效任务 ID",
                stage="submit",
                status_code=response.status_code,
            ) from exc

    async def poll_text_to_image(self, *, job_id: UUID) -> tuple[bytes, str]:
        """轮询文生图任务，返回经过校验的图片和 MIME 类型。"""
        return await self._poll(
            job_id=job_id,
            request_result=lambda: self._request_text_to_image_result(job_id=job_id),
        )

    async def poll_image_to_text(self, *, job_id: UUID) -> str:
        """轮询图片反推任务，返回 Florence-2 原始文本。"""
        return await self._poll(
            job_id=job_id,
            request_result=lambda: self._request_image_to_text_result(job_id=job_id),
        )

    async def _poll[T](
        self,
        *,
        job_id: UUID,
        request_result: Callable[[], Awaitable[T | None]],
    ) -> T:
        """按固定间隔轮询；GET 瞬断连续超过三次后失败。"""
        consecutive_network_errors = 0
        while True:
            await self._sleep(self._config.poll_interval_seconds)
            try:
                result = await request_result()
            except httpx.RequestError as exc:
                consecutive_network_errors += 1
                if consecutive_network_errors <= MAX_CONSECUTIVE_POLL_RETRIES:
                    continue
                raise NeavoTransportError(
                    "轮询 Neavo 图像任务时连续网络请求失败",
                    stage="poll",
                    job_id=job_id,
                ) from exc
            if result is None:
                consecutive_network_errors = 0
                continue
            return result

    async def _request_text_to_image_result(
        self,
        *,
        job_id: UUID,
    ) -> tuple[bytes, str] | None:
        """执行一次文生图查询；HTTP 202 表示仍在处理。"""
        async with self._http_client.stream(
            "GET",
            f"{self._config.base_url}/text_to_image/{job_id}",
            headers=self._authorization_headers(),
            timeout=self._config.request_timeout_seconds,
        ) as response:
            if response.status_code == 202:
                return None
            if response.status_code != 200:
                raise NeavoUpstreamError(
                    "Neavo 服务返回文生图终态错误",
                    stage="poll",
                    status_code=response.status_code,
                    job_id=job_id,
                )
            return await self._read_image(response=response, job_id=job_id)

    async def _request_image_to_text_result(
        self,
        *,
        job_id: UUID,
    ) -> str | None:
        """执行一次图片反推查询；HTTP 202 表示仍在处理。"""
        response = await self._http_client.get(
            f"{self._config.base_url}/image_to_text/{job_id}",
            headers=self._authorization_headers(),
            timeout=self._config.request_timeout_seconds,
        )
        if response.status_code == 202:
            return None
        if response.status_code != 200:
            raise NeavoUpstreamError(
                "Neavo 服务返回图片反推终态错误",
                stage="poll",
                status_code=response.status_code,
                job_id=job_id,
            )
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise NeavoProtocolError(
                "Neavo 图片反推结果不是有效 JSON",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            ) from exc
        if not isinstance(payload, dict):
            raise NeavoProtocolError(
                "Neavo 图片反推结果必须是 JSON 对象",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            )
        result_object = cast(dict[str, object], payload)
        text = result_object.get("text")
        if not isinstance(text, str) or not text.strip():
            raise NeavoProtocolError(
                "Neavo 图片反推结果缺少非空文本",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            )
        return text

    async def _read_image(
        self,
        *,
        response: httpx.Response,
        job_id: UUID,
    ) -> tuple[bytes, str]:
        """以有界方式读取并校验 Neavo 图片响应。"""
        content_type = response.headers.get("content-type", "")
        declared_mime_type = content_type.partition(";")[0].strip().lower()
        if not declared_mime_type.startswith("image/"):
            raise NeavoProtocolError(
                "Neavo 结果响应的 Content-Type 不是图片",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            )

        content_length = self._parse_content_length(response=response)
        if (
            content_length is not None
            and content_length > self._config.max_image_bytes
        ):
            raise NeavoProtocolError(
                "Neavo 返回的图片超过大小限制",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            )

        image_buffer = bytearray()
        async for chunk in response.aiter_bytes():
            image_buffer.extend(chunk)
            if len(image_buffer) > self._config.max_image_bytes:
                raise NeavoProtocolError(
                    "Neavo 返回的图片超过大小限制",
                    stage="validate",
                    status_code=response.status_code,
                    job_id=job_id,
                )
        image_bytes = bytes(image_buffer)
        if not image_bytes:
            raise NeavoProtocolError(
                "Neavo 返回了空图片",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            )
        try:
            detected_mime_type = detect_mime_type(image_bytes)
        except ValueError as exc:
            raise NeavoProtocolError(
                "Neavo 返回的数据无法识别为图片",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            ) from exc
        if not detected_mime_type.startswith("image/"):
            raise NeavoProtocolError(
                "Neavo 返回的数据不是受支持的图片",
                stage="validate",
                status_code=response.status_code,
                job_id=job_id,
            )
        return image_bytes, detected_mime_type

    def _validate_input_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
    ) -> str:
        """校验反推图片类型、签名与 10 MiB 固定上限。"""
        normalized_mime_type = mime_type.partition(";")[0].strip().lower()
        if normalized_mime_type not in SUPPORTED_INPUT_MIME_TYPES:
            raise NeavoProtocolError(
                "Neavo 图片反推只支持 JPEG、PNG 或 WebP",
                stage="validate",
            )
        if not image_bytes:
            raise NeavoProtocolError(
                "Neavo 图片反推输入不能为空",
                stage="validate",
            )
        if len(image_bytes) > MAX_INPUT_IMAGE_BYTES:
            raise NeavoProtocolError(
                "Neavo 图片反推输入超过 10 MiB",
                stage="validate",
            )
        try:
            detected_mime_type = detect_mime_type(image_bytes)
        except ValueError as exc:
            raise NeavoProtocolError(
                "Neavo 图片反推输入无法识别为图片",
                stage="validate",
            ) from exc
        if detected_mime_type != normalized_mime_type:
            raise NeavoProtocolError(
                "Neavo 图片反推输入与 Content-Type 不匹配",
                stage="validate",
            )
        return normalized_mime_type

    def _authorization_headers(self) -> dict[str, str]:
        """构造鉴权请求头，不暴露或记录 Token。"""
        token = self._config.api_token.get_secret_value()
        return {"Authorization": f"Bearer {token}"}

    def _parse_content_length(self, *, response: httpx.Response) -> int | None:
        """读取可选的非负 Content-Length，异常值交给流式上限兜底。"""
        raw_content_length = response.headers.get("content-length")
        if raw_content_length is None:
            return None
        try:
            content_length = int(raw_content_length)
        except ValueError:
            return None
        return content_length if content_length >= 0 else None


__all__ = [
    "MAX_CONSECUTIVE_POLL_RETRIES",
    "MAX_INPUT_IMAGE_BYTES",
    "MAX_INSTRUCTION_LENGTH",
    "SUPPORTED_INPUT_MIME_TYPES",
    "NeavoCaptionResult",
    "NeavoGenerationTimeoutError",
    "NeavoImageClient",
    "NeavoImageError",
    "NeavoImageResult",
    "NeavoProtocolError",
    "NeavoTransportError",
    "NeavoUpstreamError",
]
