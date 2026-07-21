"""仅面向配置群聊的 Neavo 指令生图插件。"""

from __future__ import annotations

import base64
from typing import ClassVar, Final, override

from app.models import At, GroupMessage, Image, NapCatId, Text
from app.plugins.base import BasePlugin
from app.utils.log import log_event

from .client import (
    MAX_INSTRUCTION_LENGTH,
    NeavoGenerationTimeoutError,
    NeavoImageClient,
    NeavoImageError,
)
from .config import NeavoImageGenerateConfig, load_neavo_image_generate_config

COMMAND_TOKEN: Final[str] = "#生图"
MAX_PROMPT_LENGTH: Final[int] = MAX_INSTRUCTION_LENGTH
CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 50


def extract_prompt(msg: GroupMessage) -> str | None:
    """拼接全部文本段，并提取独立 ``#生图`` 令牌后的提示词。"""
    text = "".join(
        segment.data.text for segment in msg.message if isinstance(segment, Text)
    ).strip()
    if text == COMMAND_TOKEN:
        return ""
    if not text.startswith(COMMAND_TOKEN):
        return None
    remainder = text[len(COMMAND_TOKEN) :]
    if not remainder or not remainder[0].isspace():
        return None
    return remainder.strip()


class NeavoImageGeneratePlugin(BasePlugin[GroupMessage]):
    """响应白名单群聊中的 Neavo 文生图指令。"""

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
        if msg.group_id not in self.group_ids or extract_prompt(msg) is None:
            return False
        return await super().add_to_queue(msg)

    @override
    async def run(self, msg: GroupMessage) -> bool:
        """校验命令、提交任务并把结果只发送给原群和原发起者。"""
        if msg.group_id not in self.group_ids:
            return False
        prompt = extract_prompt(msg)
        if prompt is None:
            return False
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

        log_event(
            level="INFO",
            event="neavo_image_generate.request.accepted",
            category="plugin",
            message="Neavo 群聊生图任务已接收",
            stage="accepted",
            group_id=msg.group_id,
            user_id=msg.user_id,
        )
        await self._send_text(
            group_id=msg.group_id,
            user_id=msg.user_id,
            text=" 已收到，正在生成图片…",
        )
        try:
            result = await self.client.generate(prompt=prompt)
        except NeavoGenerationTimeoutError as exc:
            self._log_expected_failure(msg=msg, exc=exc)
            await self._send_text(
                group_id=msg.group_id,
                user_id=msg.user_id,
                text=" 生图等待超时，请稍后再试。",
            )
            return True
        except NeavoImageError as exc:
            self._log_expected_failure(msg=msg, exc=exc)
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
        log_event(
            level="SUCCESS",
            event="neavo_image_generate.result.sent",
            category="plugin",
            message="Neavo 群聊生图结果已发送",
            stage="result",
            status_code=200,
            task_id=str(result.job_id),
            group_id=msg.group_id,
            user_id=msg.user_id,
        )
        return True

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

    def _log_expected_failure(
        self,
        *,
        msg: GroupMessage,
        exc: NeavoImageError,
    ) -> None:
        """仅记录安全的关联字段，不回显上游正文、地址或密钥。"""
        log_event(
            level="WARNING",
            event="neavo_image_generate.request.failed",
            category="plugin",
            message="Neavo 群聊生图任务失败",
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
    "NeavoImageGeneratePlugin",
    "PRIORITY",
    "extract_prompt",
]
