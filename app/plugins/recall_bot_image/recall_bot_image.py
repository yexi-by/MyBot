"""处理群聊中引用机器人图片的撤回指令。"""

from typing import ClassVar, Final, override

from app.database import GroupDataScope, StoredGroupMessage
from app.models import GroupMessage, Image, NapCatId, Reply, Response, Text
from app.plugins.base import BasePlugin
from app.utils.log import log_event, log_exception

RECALL_COMMAND: Final[str] = "#撤回"
CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 90
MAX_FAILURE_DETAIL_LENGTH: Final[int] = 160


class RecallBotImagePlugin(BasePlugin[GroupMessage]):
    """撤回当前机器人在本群发送的被引用图片消息。"""

    plugin_id: ClassVar[str] = "recall_bot_image"
    name: ClassVar[str] = "机器人图片撤回插件"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """图片撤回插件无需额外配置。"""

    @override
    async def run(self, msg: GroupMessage) -> bool:
        """识别引用撤回指令，校验目标归属并尝试撤回图片。"""
        if msg.post_type != "message" or self._extract_plain_text(msg=msg) != RECALL_COMMAND:
            return False

        reply_id = self._extract_reply_id(msg=msg)
        if reply_id is None:
            await self._send_feedback(
                msg=msg,
                text=f"请回复机器人发送的图片，再发送 {RECALL_COMMAND}。",
            )
            return True

        try:
            stored_message = await self.context.group_messages.get_active(
                scope=GroupDataScope(
                    bot_id=msg.self_id,
                    group_id=msg.group_id,
                ),
                message_id=reply_id,
            )
        except Exception as exc:
            log_exception(
                event="recall_bot_image.lookup_failed",
                category="plugin",
                message="读取被引用消息失败",
                exc=exc,
                group_id=msg.group_id,
                user_id=msg.user_id,
                command_message_id=msg.message_id,
                target_message_id=reply_id,
            )
            await self._send_feedback(
                msg=msg,
                text="读取被引用消息失败，请稍后重试。",
            )
            return True

        if stored_message is None:
            await self._reject_target(
                msg=msg,
                target_message_id=reply_id,
                reason="not_found",
                feedback="找不到被引用的消息，或该消息已撤回。",
            )
            return True

        if not self._is_current_bot_message(msg=msg, target=stored_message):
            await self._reject_target(
                msg=msg,
                target_message_id=reply_id,
                reason="not_current_bot_message",
                feedback="只能撤回当前机器人自己发送的图片。",
            )
            return True

        if not any(isinstance(segment, Image) for segment in stored_message.segments):
            await self._reject_target(
                msg=msg,
                target_message_id=reply_id,
                reason="without_image",
                feedback="被引用的机器人消息不包含图片，无法撤回。",
            )
            return True

        await self._recall_image(
            msg=msg,
            target_message_id=stored_message.message_id,
        )
        return True

    def _extract_plain_text(self, *, msg: GroupMessage) -> str:
        """拼接文本消息段并去掉首尾空白。"""
        return "".join(
            segment.data.text
            for segment in msg.message
            if isinstance(segment, Text)
        ).strip()

    def _extract_reply_id(self, *, msg: GroupMessage) -> NapCatId | None:
        """提取当前消息引用的首个消息 ID。"""
        for segment in msg.message:
            if isinstance(segment, Reply):
                return segment.data.id
        return None

    def _is_current_bot_message(
        self, *, msg: GroupMessage, target: StoredGroupMessage
    ) -> bool:
        """严格判断目标是否为当前机器人在当前群保存的出站消息。"""
        return (
            target.scope.group_id == msg.group_id
            and target.scope.bot_id == msg.self_id
            and target.direction == "outgoing"
            and target.sender_id == msg.self_id
        )

    async def _reject_target(
        self,
        *,
        msg: GroupMessage,
        target_message_id: NapCatId,
        reason: str,
        feedback: str,
    ) -> None:
        """记录未通过安全校验的目标并向触发者说明原因。"""
        log_event(
            level="WARNING",
            event="recall_bot_image.target_rejected",
            category="plugin",
            message="被引用消息不符合机器人图片撤回条件",
            group_id=msg.group_id,
            user_id=msg.user_id,
            command_message_id=msg.message_id,
            target_message_id=target_message_id,
            reason=reason,
        )
        await self._send_feedback(msg=msg, text=feedback)

    async def _recall_image(
        self, *, msg: GroupMessage, target_message_id: NapCatId
    ) -> None:
        """调用带回包的撤回接口，并根据明确结果反馈成功或失败。"""
        try:
            response = await self.context.bot.delete_msg_with_response(
                message_id=target_message_id
            )
        except Exception as exc:
            log_exception(
                event="recall_bot_image.request_failed",
                category="plugin",
                message="机器人图片撤回请求异常",
                exc=exc,
                group_id=msg.group_id,
                user_id=msg.user_id,
                command_message_id=msg.message_id,
                target_message_id=target_message_id,
            )
            detail = self._normalize_failure_detail(
                str(exc) or type(exc).__name__
            )
            await self._send_feedback(
                msg=msg,
                text=(
                    f"撤回请求未完成：{detail}。请查看原图片是否仍在；"
                    "请求可能受到 QQ 撤回时限、权限或接口状态限制。"
                ),
            )
            return

        if response.status != "ok" or response.retcode != 0:
            detail = self._response_failure_detail(response=response)
            log_event(
                level="WARNING",
                event="recall_bot_image.rejected",
                category="plugin",
                message="NapCat 拒绝机器人图片撤回请求",
                group_id=msg.group_id,
                user_id=msg.user_id,
                command_message_id=msg.message_id,
                target_message_id=target_message_id,
                response_status=response.status,
                response_retcode=response.retcode,
                response_message=response.message,
                response_wording=response.wording,
            )
            await self._send_feedback(
                msg=msg,
                text=(
                    f"撤回图片失败：{detail}。"
                    "可能受到 QQ 撤回时限、权限或接口状态限制。"
                ),
            )
            return

        log_event(
            level="SUCCESS",
            event="recall_bot_image.succeeded",
            category="plugin",
            message="机器人图片撤回成功",
            group_id=msg.group_id,
            user_id=msg.user_id,
            command_message_id=msg.message_id,
            target_message_id=target_message_id,
        )
        await self._send_feedback(msg=msg, text="图片已撤回。")

    def _response_failure_detail(self, *, response: Response) -> str:
        """从 NapCat 失败回包提取适合群内展示的简短原因。"""
        detail = response.wording.strip() or response.message.strip()
        if not detail:
            detail = f"NapCat 返回 retcode={response.retcode}"
        return self._normalize_failure_detail(detail)

    def _normalize_failure_detail(self, detail: str) -> str:
        """压缩错误空白并限制群内错误文本长度。"""
        normalized = " ".join(detail.split())
        if len(normalized) <= MAX_FAILURE_DETAIL_LENGTH:
            return normalized
        return f"{normalized[:MAX_FAILURE_DETAIL_LENGTH]}…"

    async def _send_feedback(self, *, msg: GroupMessage, text: str) -> None:
        """向触发者反馈结果；反馈发送失败只记录日志，不改变撤回结果。"""
        try:
            _ = await self.context.bot.send_msg(
                group_id=msg.group_id,
                at=msg.user_id,
                text=text,
            )
        except Exception as exc:
            log_exception(
                event="recall_bot_image.feedback_failed",
                category="plugin",
                message="机器人图片撤回结果发送失败",
                exc=exc,
                group_id=msg.group_id,
                user_id=msg.user_id,
                command_message_id=msg.message_id,
            )
