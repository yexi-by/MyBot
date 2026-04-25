"""群成员变动和加群申请提醒插件。"""

import base64
from typing import ClassVar, Final, override

from app.config.plugin_config import load_plugin_config
from app.models import (
    GroupDecreaseEvent,
    GroupIncreaseEvent,
    GroupRequestEvent,
    Image,
    MessageSegment,
    NapCatId,
    Response,
    StrictModel,
    Text,
)
from app.plugins.base import BasePlugin
from app.utils.log import log_event

CONFIG_SECTION: Final[str] = "group_notice"
AVATAR_URL_TEMPLATE: Final[str] = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
UNKNOWN_NICKNAME: Final[str] = "未知用户"
CONSUMERS_COUNT: Final[int] = 5
PRIORITY: Final[int] = 10


class GroupNoticeConfig(StrictModel):
    """群成员变动提醒插件配置。"""

    group_ids: list[NapCatId]
    send_avatar: bool = True


class GroupNoticePlugin(
    BasePlugin[GroupRequestEvent | GroupIncreaseEvent | GroupDecreaseEvent]
):
    """把加群申请和成员变动通知发送到对应群聊。"""

    name: ClassVar[str] = "群成员变动提醒插件"
    consumers_count: ClassVar[int] = CONSUMERS_COUNT
    priority: ClassVar[int] = PRIORITY

    @override
    def setup(self) -> None:
        """读取启用群列表。"""
        self.config: GroupNoticeConfig = load_plugin_config(
            section_name=CONFIG_SECTION,
            model_cls=GroupNoticeConfig,
        )
        self.group_ids: set[NapCatId] = set(self.config.group_ids)

    @override
    async def run(
        self, msg: GroupRequestEvent | GroupIncreaseEvent | GroupDecreaseEvent
    ) -> bool:
        """处理群成员变动相关事件。"""
        if msg.group_id not in self.group_ids:
            return False
        text_content = await self._build_notice_text(msg=msg)
        if text_content is None:
            return False
        message_segments: list[MessageSegment] = [Text.new(text_content)]
        avatar_segment = await self._build_avatar_segment(user_id=msg.user_id)
        if avatar_segment is not None:
            message_segments.append(avatar_segment)
        _ = await self.context.bot.send_msg(
            group_id=msg.group_id,
            message_segment=message_segments,
        )
        return True

    async def _build_notice_text(
        self, msg: GroupRequestEvent | GroupIncreaseEvent | GroupDecreaseEvent
    ) -> str | None:
        """根据事件类型生成群提醒文案。"""
        nickname = await self._load_nickname(user_id=msg.user_id)
        user_label = f"{nickname} ({msg.user_id})"
        if isinstance(msg, GroupRequestEvent):
            return self._format_group_request(msg=msg, user_label=user_label)
        if isinstance(msg, GroupIncreaseEvent):
            return self._format_group_increase(msg=msg, user_label=user_label)
        return self._format_group_decrease(msg=msg, user_label=user_label)

    def _format_group_request(
        self, *, msg: GroupRequestEvent, user_label: str
    ) -> str:
        """生成加群申请提醒。"""
        comment = msg.comment.strip()
        if comment == "":
            comment = "无"
        request_type = "邀请入群" if msg.sub_type == "invite" else "申请入群"
        return "\n".join(
            [
                "新的加群请求",
                "",
                f"用户: {user_label}",
                f"类型: {request_type}",
                f"验证信息: {comment}",
                "",
                "请管理员尽快处理。",
            ]
        )

    def _format_group_increase(
        self, *, msg: GroupIncreaseEvent, user_label: str
    ) -> str:
        """生成成员加入提醒。"""
        status = "被邀请入群" if msg.sub_type == "invite" else "已加入群聊"
        lines = [
            "成员加入提醒",
            "",
            f"用户: {user_label}",
            f"状态: {status}",
        ]
        if msg.operator_id is not None and msg.operator_id != msg.user_id:
            lines.append(f"操作者: {msg.operator_id}")
        return "\n".join(lines)

    def _format_group_decrease(
        self, *, msg: GroupDecreaseEvent, user_label: str
    ) -> str | None:
        """生成成员离开提醒。"""
        match msg.sub_type:
            case "leave":
                title = "成员离开提醒"
                status = "主动退群"
            case "kick":
                title = "成员变动提醒"
                status = "被移出群聊"
            case "kick_me":
                title = "机器人变动提醒"
                status = "机器人被移出群聊"
            case "disband":
                return None
            case _:
                title = "成员变动提醒"
                status = msg.sub_type
        lines = [
            title,
            "",
            f"用户: {user_label}",
            f"状态: {status}",
        ]
        if msg.operator_id != msg.user_id:
            lines.append(f"操作者: {msg.operator_id}")
        return "\n".join(lines)

    async def _load_nickname(self, *, user_id: NapCatId) -> str:
        """读取用户昵称，失败时返回兜底称呼。"""
        try:
            response = await self.context.bot.get_stranger_info(user_id=user_id)
        except Exception as exc:
            log_event(
                level="WARNING",
                event="group_notice.nickname_load_failed",
                category="plugin",
                message="读取用户昵称失败，使用兜底称呼",
                user_id=user_id,
                error=str(exc),
            )
            return UNKNOWN_NICKNAME
        nickname = self._extract_nickname(response=response)
        if nickname is None:
            return UNKNOWN_NICKNAME
        return nickname

    def _extract_nickname(self, *, response: Response) -> str | None:
        """从 NapCat 响应中提取昵称。"""
        data = response.data
        if not isinstance(data, dict):
            return None
        nickname = data.get("nickname")
        if not isinstance(nickname, str):
            return None
        cleaned_nickname = nickname.strip()
        if cleaned_nickname == "":
            return None
        return cleaned_nickname

    async def _build_avatar_segment(self, *, user_id: NapCatId) -> Image | None:
        """下载 QQ 头像并转换为图片消息段。"""
        if not self.config.send_avatar:
            return None
        avatar_url = AVATAR_URL_TEMPLATE.format(user_id=user_id)
        try:
            async with self.context.direct_httpx.stream("GET", avatar_url) as response:
                _ = response.raise_for_status()
                content = await response.aread()
        except Exception as exc:
            log_event(
                level="WARNING",
                event="group_notice.avatar_load_failed",
                category="plugin",
                message="读取 QQ 头像失败，将只发送文字提醒",
                user_id=user_id,
                url=avatar_url,
                error=str(exc),
            )
            return None
        image_base64 = base64.b64encode(content).decode("utf-8")
        return Image.new(f"base64://{image_base64}")
