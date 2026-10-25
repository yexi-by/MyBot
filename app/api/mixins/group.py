"""NapCat 群聊相关 Action。"""

from app.models import NapCatId

from .base import BaseMixin


class GroupMixin(BaseMixin):
    """群聊相关 API。"""

    async def set_group_ban(
        self, group_id: NapCatId, user_id: NapCatId, duration: int
    ) -> None:
        """群禁言。"""
        await self._send_action(
            "set_group_ban",
            self._build_params(group_id=group_id, user_id=user_id, duration=duration),
        )
