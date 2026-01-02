"""系统相关 Mixin 类"""

from app.models.api.payloads import system as system_payload

from .base import BaseMixin


class SystemMixin(BaseMixin):
    """系统相关的 API 接口"""

    async def get_version_info(self):
        """获取版本信息"""
        echo = self._generate_echo()
        payload = system_payload.GetVersionInfoPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def nc_get_packet_status(self):
        """获取packet状态"""
        echo = self._generate_echo()
        payload = system_payload.NcGetPacketStatusPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_robot_uin_range(self):
        """获取机器人账号范围"""
        echo = self._generate_echo()
        payload = system_payload.GetRobotUinRangePayload(echo=echo)
        return await self._send_and_wait(payload)

    async def bot_exit(self) -> None:
        """账号退出"""
        payload = system_payload.BotExitPayload()
        await self._send_payload(payload)

    async def can_send_image(self):
        """检查是否可以发送图片"""
        echo = self._generate_echo()
        payload = system_payload.CanSendImagePayload(echo=echo)
        return await self._send_and_wait(payload)

    async def can_send_record(self):
        """检查是否可以发送语音"""
        echo = self._generate_echo()
        payload = system_payload.CanSendRecordPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def ocr_image(self, image: str):
        """OCR 图片识别"""
        echo = self._generate_echo()
        payload = system_payload.OcrImagePayload(
            params=system_payload.OcrImageParams(image=image),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def translate_en2zh(self, words: list[str]):
        """英译中"""
        echo = self._generate_echo()
        payload = system_payload.TranslateEn2ZhPayload(
            params=system_payload.TranslateEn2ZhParams(words=words),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_input_status(
        self,
        event_type: str,
        group_id: int | None = None,
        user_id: int | None = None,
    ) -> None:
        """设置输入状态"""
        payload = system_payload.SetInputStatusPayload(
            params=system_payload.SetInputStatusParams(
                group_id=group_id, user_id=user_id, eventType=event_type
            )
        )
        await self._send_payload(payload)

    async def get_cookies(self, domain: str):
        """获取cookies"""
        echo = self._generate_echo()
        payload = system_payload.GetCookiesPayload(
            params=system_payload.GetCookiesParams(domain=domain),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_csrf_token(self):
        """获取 CSRF Token"""
        echo = self._generate_echo()
        payload = system_payload.GetCsrfTokenPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_credentials(self, domain: str):
        """获取 QQ 相关接口凭证"""
        echo = self._generate_echo()
        payload = system_payload.GetCredentialsPayload(
            params=system_payload.GetCredentialsParams(domain=domain),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def nc_get_rkey(self):
        """nc获取rkey"""
        echo = self._generate_echo()
        payload = system_payload.NcGetRkeyPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_rkey(self):
        """获取rkey"""
        echo = self._generate_echo()
        payload = system_payload.GetRkeyPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_clientkey(self):
        """获取clientkey"""
        echo = self._generate_echo()
        payload = system_payload.GetClientkeyPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_ai_record(
        self, character: str, text: str, group_id: int | None = None
    ):
        """获取AI语音"""
        echo = self._generate_echo()
        payload = system_payload.GetAiRecordPayload(
            params=system_payload.GetAiRecordParams(
                character=character, text=text, group_id=group_id
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def check_url_safely(self, url: str):
        """检查链接安全性"""
        echo = self._generate_echo()
        payload = system_payload.CheckUrlSafelyPayload(
            params=system_payload.CheckUrlSafelyParams(url=url),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_mini_app_ark(
        self, type: str, title: str, desc: str, pic_url: str, jump_url: str
    ):
        """获取小程序卡片"""
        echo = self._generate_echo()
        payload = system_payload.GetMiniAppArkPayload(
            params=system_payload.GetMiniAppArkParams(
                type=type, title=title, desc=desc, picUrl=pic_url, jumpUrl=jump_url
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_collection_list(self, category: int, count: int = 10):
        """获取收藏列表"""
        echo = self._generate_echo()
        payload = system_payload.GetCollectionListPayload(
            params=system_payload.GetCollectionListParams(
                category=category, count=count
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)
