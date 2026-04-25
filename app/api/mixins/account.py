"""NapCat 账号相关 Action。"""

from typing import Literal

from app.models import NapCatId, Response

from .base import BaseMixin


class AccountMixin(BaseMixin):
    """账号相关 API。"""

    async def mark_msg_as_read(
        self, group_id: NapCatId | None = None, user_id: NapCatId | None = None
    ) -> None:
        """设置消息已读。"""
        await self._send_action(
            "mark_msg_as_read",
            self._build_params(group_id=group_id, user_id=user_id),
        )

    async def mark_private_msg_as_read(self, user_id: NapCatId) -> None:
        """设置私聊已读。"""
        await self._send_action(
            "mark_private_msg_as_read", self._build_params(user_id=user_id)
        )

    async def mark_group_msg_as_read(self, group_id: NapCatId) -> None:
        """设置群聊已读。"""
        await self._send_action(
            "mark_group_msg_as_read", self._build_params(group_id=group_id)
        )

    async def get_recent_contact(self, count: int = 10) -> Response:
        """获取最近消息列表。"""
        return await self._call_action(
            "get_recent_contact", self._build_params(count=count)
        )

    async def mark_all_as_read(self) -> None:
        """设置所有消息已读。"""
        await self._send_action("_mark_all_as_read")

    async def send_like(self, user_id: NapCatId, times: int = 1) -> None:
        """发送资料卡点赞。"""
        await self._send_action(
            "send_like", self._build_params(user_id=user_id, times=times)
        )

    async def set_friend_add_request(
        self, flag: str, approve: bool, remark: str | None = None
    ) -> None:
        """处理好友请求。"""
        await self._send_action(
            "set_friend_add_request",
            self._build_params(flag=flag, approve=approve, remark=remark),
        )

    async def get_stranger_info(self, user_id: NapCatId) -> Response:
        """获取陌生人信息。"""
        return await self._call_action(
            "get_stranger_info", self._build_params(user_id=user_id)
        )

    async def get_friend_list(self, no_cache: bool = False) -> Response:
        """获取好友列表。"""
        return await self._call_action(
            "get_friend_list", self._build_params(no_cache=no_cache)
        )

    async def get_friends_with_category(self) -> Response:
        """获取好友分组列表。"""
        return await self._call_action("get_friends_with_category")

    async def set_qq_profile(
        self,
        nickname: str,
        personal_note: str | None = None,
        sex: Literal["male", "female", "unknown"] | None = None,
    ) -> None:
        """设置账号资料。"""
        await self._send_action(
            "set_qq_profile",
            self._build_params(
                nickname=nickname, personal_note=personal_note, sex=sex
            ),
        )

    async def delete_friend(
        self,
        user_id: NapCatId | None = None,
        friend_id: NapCatId | None = None,
        temp_block: bool = False,
        temp_both_del: bool = False,
    ) -> None:
        """删除好友。"""
        await self._send_action(
            "delete_friend",
            self._build_params(
                user_id=user_id,
                friend_id=friend_id,
                temp_block=temp_block,
                temp_both_del=temp_both_del,
            ),
        )

    async def ark_share_peer(
        self,
        group_id: NapCatId | None = None,
        user_id: NapCatId | None = None,
        phone_number: str | None = None,
    ) -> Response:
        """获取推荐好友或群聊卡片。"""
        return await self._call_action(
            "ArkSharePeer",
            self._build_params(
                group_id=group_id, user_id=user_id, phoneNumber=phone_number
            ),
        )

    async def ark_share_group(self, group_id: NapCatId) -> Response:
        """获取推荐群聊卡片。"""
        return await self._call_action(
            "ArkShareGroup", self._build_params(group_id=group_id)
        )

    async def set_online_status(
        self, status: int, ext_status: int, battery_status: int = 0
    ) -> None:
        """设置在线状态。"""
        await self._send_action(
            "set_online_status",
            self._build_params(
                status=status, ext_status=ext_status, battery_status=battery_status
            ),
        )

    async def set_diy_online_status(
        self, face_id: int, face_type: int | None = None, wording: str | None = None
    ) -> None:
        """设置自定义在线状态。"""
        await self._send_action(
            "set_diy_online_status",
            self._build_params(face_id=face_id, face_type=face_type, wording=wording),
        )

    async def set_qq_avatar(self, file: str) -> None:
        """设置头像。"""
        await self._send_action("set_qq_avatar", self._build_params(file=file))

    async def create_collection(self, raw_data: str, brief: str) -> None:
        """创建收藏。"""
        await self._send_action(
            "create_collection", self._build_params(rawData=raw_data, brief=brief)
        )

    async def set_self_longnick(self, long_nick: str) -> None:
        """设置个性签名。"""
        await self._send_action(
            "set_self_longnick", self._build_params(longNick=long_nick)
        )

    async def fetch_custom_face(self, count: int = 40) -> Response:
        """获取收藏表情。"""
        return await self._call_action(
            "fetch_custom_face", self._build_params(count=count)
        )

    async def get_profile_like(
        self, user_id: NapCatId | None = None, start: int = 0, count: int = 10
    ) -> Response:
        """获取点赞列表。"""
        return await self._call_action(
            "get_profile_like",
            self._build_params(user_id=user_id, start=start, count=count),
        )

    async def nc_get_user_status(self, user_id: NapCatId) -> Response:
        """获取用户状态。"""
        return await self._call_action(
            "nc_get_user_status", self._build_params(user_id=user_id)
        )

    async def get_unidirectional_friend_list(self) -> Response:
        """获取单向好友列表。"""
        return await self._call_action("get_unidirectional_friend_list")

    async def get_login_info(self) -> Response:
        """获取登录号信息。"""
        return await self._call_action("get_login_info")

    async def get_status(self) -> Response:
        """获取账号状态。"""
        return await self._call_action("get_status")

    async def get_online_clients(self) -> Response:
        """获取在线客户端列表。"""
        return await self._call_action("get_online_clients")

    async def get_model_show(self, model: str) -> Response:
        """获取在线机型。"""
        return await self._call_action(
            "_get_model_show", self._build_params(model=model)
        )

    async def set_model_show(self, model: str, model_show: str) -> None:
        """设置在线机型。"""
        await self._send_action(
            "_set_model_show",
            self._build_params(model=model, model_show=model_show),
        )

    async def get_doubt_friends_add_request(self, count: int = 50) -> Response:
        """获取被过滤好友请求。"""
        return await self._call_action(
            "get_doubt_friends_add_request", self._build_params(count=count)
        )

    async def set_doubt_friends_add_request(self, flag: str, approve: bool) -> None:
        """处理被过滤好友请求。"""
        await self._send_action(
            "set_doubt_friends_add_request",
            self._build_params(flag=flag, approve=approve),
        )

    async def set_friend_remark(self, user_id: NapCatId, remark: str) -> None:
        """设置好友备注。"""
        await self._send_action(
            "set_friend_remark", self._build_params(user_id=user_id, remark=remark)
        )
