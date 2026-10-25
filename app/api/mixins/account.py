"""NapCat 账号相关 Action。"""

from app.models import NapCatId, Response

from .base import BaseMixin


class AccountMixin(BaseMixin):
    """账号相关 API。"""

    async def get_stranger_info(
        self, user_id: NapCatId, no_cache: bool = False
    ) -> Response:
        """获取陌生人信息。"""
        return await self._call_action(
            "get_stranger_info",
            self._build_params(user_id=user_id, no_cache=no_cache),
        )
