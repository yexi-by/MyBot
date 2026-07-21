"""Neavo 指令生图 HTTP 客户端。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

import httpx

from app.utils.file_type import detect_mime_type

from .config import NeavoImageGenerateConfig

type NeavoRequestStage = Literal["submit", "poll", "validate"]
type SleepFunction = Callable[[float], Awaitable[None]]

MAX_CONSECUTIVE_POLL_RETRIES = 3
MAX_INSTRUCTION_LENGTH = 4096


class NeavoImageError(RuntimeError):
    """Neavo 生图请求的可预期失败。"""

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
    """Neavo 响应不符合约定协议。"""


class NeavoUpstreamError(NeavoImageError):
    """Neavo 服务返回终态错误状态。"""


class NeavoGenerationTimeoutError(NeavoImageError):
    """生图任务超过总等待期限。"""


@dataclass(frozen=True, slots=True)
class NeavoImageResult:
    """一项完成的 Neavo 生图任务。"""

    job_id: UUID
    image_bytes: bytes
    mime_type: str


class NeavoImageClient:
    """通过 Neavo HTTP 任务接口提交指令并轮询图片。"""

    def __init__(
        self,
        *,
        config: NeavoImageGenerateConfig,
        http_client: httpx.AsyncClient,
        sleep: SleepFunction = asyncio.sleep,
    ) -> None:
        """绑定插件配置、共享 HTTP 客户端与可测试的休眠函数。"""
        self._config: NeavoImageGenerateConfig = config
        self._http_client: httpx.AsyncClient = http_client
        self._sleep: SleepFunction = sleep

    async def generate(self, prompt: str) -> NeavoImageResult:
        """在总期限内提交单个任务并等待最终图片。"""
        job_id: UUID | None = None
        try:
            async with asyncio.timeout(self._config.generation_timeout_seconds):
                job_id = await self.submit(prompt=prompt)
                image_bytes, mime_type = await self.poll_result(job_id=job_id)
        except TimeoutError as exc:
            raise NeavoGenerationTimeoutError(
                "Neavo 生图任务等待超时",
                stage="poll" if job_id is not None else "submit",
                job_id=job_id,
            ) from exc
        return NeavoImageResult(
            job_id=job_id,
            image_bytes=image_bytes,
            mime_type=mime_type,
        )

    async def submit(self, *, prompt: str) -> UUID:
        """提交一次生图任务；为避免重复任务，不自动重试 POST。"""
        if not 1 <= len(prompt) <= MAX_INSTRUCTION_LENGTH:
            raise NeavoProtocolError(
                f"Neavo 生图指令长度必须为 1～{MAX_INSTRUCTION_LENGTH} 个字符",
                stage="submit",
            )
        try:
            response = await self._http_client.post(
                f"{self._config.base_url}/new",
                headers=self._authorization_headers(),
                json={"instruction": prompt},
                timeout=self._config.request_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise NeavoTransportError(
                "提交 Neavo 生图任务时网络请求失败",
                stage="submit",
            ) from exc

        if response.status_code != 202:
            raise NeavoUpstreamError(
                "Neavo 服务拒绝了生图任务",
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

    async def poll_result(self, *, job_id: UUID) -> tuple[bytes, str]:
        """按固定间隔轮询任务，返回经过校验的图片和 MIME 类型。"""
        consecutive_network_errors = 0
        while True:
            await self._sleep(self._config.poll_interval_seconds)
            try:
                result = await self._request_result(job_id=job_id)
            except httpx.RequestError as exc:
                consecutive_network_errors += 1
                if consecutive_network_errors <= MAX_CONSECUTIVE_POLL_RETRIES:
                    continue
                raise NeavoTransportError(
                    "轮询 Neavo 生图结果时连续网络请求失败",
                    stage="poll",
                    job_id=job_id,
                ) from exc
            if result is None:
                consecutive_network_errors = 0
                continue
            return result

    async def _request_result(self, *, job_id: UUID) -> tuple[bytes, str] | None:
        """执行一次结果查询；400 表示任务仍在处理中。"""
        async with self._http_client.stream(
            "GET",
            f"{self._config.base_url}/result/{job_id}",
            headers=self._authorization_headers(),
            timeout=self._config.request_timeout_seconds,
        ) as response:
            if response.status_code == 400:
                return None
            if response.status_code != 200:
                raise NeavoUpstreamError(
                    "Neavo 服务返回生图终态错误",
                    stage="poll",
                    status_code=response.status_code,
                    job_id=job_id,
                )
            return await self._read_image(response=response, job_id=job_id)

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
    "MAX_INSTRUCTION_LENGTH",
    "NeavoGenerationTimeoutError",
    "NeavoImageClient",
    "NeavoImageError",
    "NeavoImageResult",
    "NeavoProtocolError",
    "NeavoTransportError",
    "NeavoUpstreamError",
]
