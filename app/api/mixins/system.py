"""NapCat 系统相关 Action。"""

from app.models import NapCatId, Response

from .base import BaseMixin


class SystemMixin(BaseMixin):
    """系统相关 API。"""

    async def get_version_info(self) -> Response:
        """获取版本信息。"""
        return await self._call_action("get_version_info")

    async def nc_get_packet_status(self) -> Response:
        """获取 Packet 状态。"""
        return await self._call_action("nc_get_packet_status")

    async def get_robot_uin_range(self) -> Response:
        """获取机器人账号范围。"""
        return await self._call_action("get_robot_uin_range")

    async def bot_exit(self) -> None:
        """账号退出。"""
        await self._send_action("bot_exit")

    async def can_send_image(self) -> Response:
        """检查是否可以发送图片。"""
        return await self._call_action("can_send_image")

    async def can_send_record(self) -> Response:
        """检查是否可以发送语音。"""
        return await self._call_action("can_send_record")

    async def ocr_image(self, image: str) -> Response:
        """OCR 图片识别。"""
        return await self._call_action("ocr_image", self._build_params(image=image))

    async def translate_en2zh(self, words: list[str]) -> Response:
        """英译中。"""
        return await self._call_action(
            "translate_en2zh", self._build_params(words=words)
        )

    async def set_input_status(
        self,
        event_type: str,
        group_id: NapCatId | None = None,
        user_id: NapCatId | None = None,
    ) -> None:
        """设置输入状态。"""
        await self._send_action(
            "set_input_status",
            self._build_params(
                group_id=group_id, user_id=user_id, eventType=event_type
            ),
        )

    async def get_cookies(self, domain: str) -> Response:
        """获取 Cookies。"""
        return await self._call_action(
            "get_cookies", self._build_params(domain=domain)
        )

    async def get_csrf_token(self) -> Response:
        """获取 CSRF Token。"""
        return await self._call_action("get_csrf_token")

    async def get_credentials(self, domain: str) -> Response:
        """获取 QQ 相关接口凭证。"""
        return await self._call_action(
            "get_credentials", self._build_params(domain=domain)
        )

    async def nc_get_rkey(self) -> Response:
        """获取 NapCat rkey。"""
        return await self._call_action("nc_get_rkey")

    async def get_rkey(self) -> Response:
        """获取 rkey。"""
        return await self._call_action("get_rkey")

    async def get_clientkey(self) -> Response:
        """获取 clientkey。"""
        return await self._call_action("get_clientkey")

    async def get_ai_record(
        self, character: str, text: str, group_id: NapCatId | None = None
    ) -> Response:
        """获取 AI 语音。"""
        return await self._call_action(
            "get_ai_record",
            self._build_params(character=character, text=text, group_id=group_id),
        )

    async def check_url_safely(self, url: str) -> Response:
        """检查链接安全性。"""
        return await self._call_action(
            "check_url_safely", self._build_params(url=url)
        )

    async def get_mini_app_ark(
        self, type: str, title: str, desc: str, pic_url: str, jump_url: str
    ) -> Response:
        """获取小程序卡片。"""
        return await self._call_action(
            "get_mini_app_ark",
            self._build_params(
                type=type, title=title, desc=desc, picUrl=pic_url, jumpUrl=jump_url
            ),
        )

    async def get_collection_list(self, category: int, count: int = 10) -> Response:
        """获取收藏列表。"""
        return await self._call_action(
            "get_collection_list",
            self._build_params(category=category, count=count),
        )
