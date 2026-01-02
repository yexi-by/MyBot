"""账号相关 Mixin 类"""

from app.models.api.payloads import account as account_payload

from .base import BaseMixin


class AccountMixin(BaseMixin):
    """账号相关的 API 接口"""

    async def mark_msg_as_read(
        self, group_id: int | None = None, user_id: int | None = None
    ) -> None:
        """设置消息已读"""
        payload = account_payload.MarkMsgAsReadPayload(
            params=account_payload.MarkMsgAsReadParams(
                group_id=group_id, user_id=user_id
            )
        )
        await self._send_payload(payload)

    async def mark_private_msg_as_read(self, user_id: int) -> None:
        """设置私聊已读"""
        payload = account_payload.MarkPrivateMsgAsReadPayload(
            params=account_payload.MarkPrivateMsgAsReadParams(user_id=user_id)
        )
        await self._send_payload(payload)

    async def mark_group_msg_as_read(self, group_id: int) -> None:
        """设置群聊已读"""
        payload = account_payload.MarkGroupMsgAsReadPayload(
            params=account_payload.MarkGroupMsgAsReadParams(group_id=group_id)
        )
        await self._send_payload(payload)

    async def get_recent_contact(self, count: int = 10):
        """获取最近消息列表"""
        echo = self._generate_echo()
        payload = account_payload.GetRecentContactPayload(
            params=account_payload.GetRecentContactParams(count=count),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def mark_all_as_read(self) -> None:
        """设置所有消息已读"""
        payload = account_payload.MarkAllAsReadPayload()
        await self._send_payload(payload)

    async def send_like(self, user_id: int, times: int = 1) -> None:
        """点赞"""
        payload = account_payload.SendLikePayload(
            params=account_payload.SendLikeParams(user_id=user_id, times=times)
        )
        await self._send_payload(payload)

    async def set_friend_add_request(
        self, flag: str, approve: bool, remark: str | None = None
    ) -> None:
        """处理好友请求"""
        payload = account_payload.SetFriendAddRequestPayload(
            params=account_payload.SetFriendAddRequestParams(
                flag=flag, approve=approve, remark=remark
            )
        )
        await self._send_payload(payload)

    async def get_stranger_info(self, user_id: int):
        """获取账号信息"""
        echo = self._generate_echo()
        payload = account_payload.GetStrangerInfoPayload(
            params=account_payload.GetStrangerInfoParams(user_id=user_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_friend_list(self, no_cache: bool = False):
        """获取好友列表"""
        echo = self._generate_echo()
        payload = account_payload.GetFriendListPayload(
            params=account_payload.GetFriendListParams(no_cache=no_cache),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_friends_with_category(self):
        """获取好友分组列表"""
        echo = self._generate_echo()
        payload = account_payload.GetFriendsWithCategoryPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def set_qq_profile(
        self, nickname: str, personal_note: str | None = None, sex: str | None = None
    ) -> None:
        """设置账号信息"""
        payload = account_payload.SetQqProfilePayload(
            params=account_payload.SetQqProfileParams(
                nickname=nickname, personal_note=personal_note, sex=sex
            )
        )
        await self._send_payload(payload)

    async def delete_friend(
        self,
        user_id: int | None = None,
        friend_id: int | None = None,
        temp_block: bool = False,
        temp_both_del: bool = False,
    ) -> None:
        """删除好友"""
        payload = account_payload.DeleteFriendPayload(
            params=account_payload.DeleteFriendParams(
                user_id=user_id,
                friend_id=friend_id,
                temp_block=temp_block,
                temp_both_del=temp_both_del,
            )
        )
        await self._send_payload(payload)

    async def ark_share_peer(
        self,
        group_id: int | None = None,
        user_id: int | None = None,
        phone_number: str | None = None,
    ):
        """获取推荐好友/群聊卡片"""
        echo = self._generate_echo()
        payload = account_payload.ArkSharePeerPayload(
            params=account_payload.ArkSharePeerParams(
                group_id=group_id, user_id=user_id, phoneNumber=phone_number
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def ark_share_group(self, group_id: str):
        """获取推荐群聊卡片"""
        echo = self._generate_echo()
        payload = account_payload.ArkShareGroupPayload(
            params=account_payload.ArkShareGroupParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_online_status(
        self, status: int, ext_status: int, battery_status: int = 0
    ) -> None:
        """设置在线状态"""
        payload = account_payload.SetOnlineStatusPayload(
            params=account_payload.SetOnlineStatusParams(
                status=status, ext_status=ext_status, battery_status=battery_status
            )
        )
        await self._send_payload(payload)

    async def set_diy_online_status(
        self, face_id: int, face_type: int | None = None, wording: str | None = None
    ) -> None:
        """设置自定义在线状态"""
        payload = account_payload.SetDiyOnlineStatusPayload(
            params=account_payload.SetDiyOnlineStatusParams(
                face_id=face_id, face_type=face_type, wording=wording
            )
        )
        await self._send_payload(payload)

    async def set_qq_avatar(self, file: str) -> None:
        """设置头像"""
        payload = account_payload.SetQqAvatarPayload(
            params=account_payload.SetQqAvatarParams(file=file)
        )
        await self._send_payload(payload)

    async def create_collection(self, raw_data: str, brief: str) -> None:
        """创建收藏"""
        payload = account_payload.CreateCollectionPayload(
            params=account_payload.CreateCollectionParams(rawData=raw_data, brief=brief)
        )
        await self._send_payload(payload)

    async def set_self_longnick(self, long_nick: str) -> None:
        """设置个性签名"""
        payload = account_payload.SetSelfLongnickPayload(
            params=account_payload.SetSelfLongnickParams(longNick=long_nick)
        )
        await self._send_payload(payload)

    async def fetch_custom_face(self, count: int = 40):
        """获取收藏表情"""
        echo = self._generate_echo()
        payload = account_payload.FetchCustomFacePayload(
            params=account_payload.FetchCustomFaceParams(count=count),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_profile_like(
        self, user_id: int | None = None, start: int = 0, count: int = 10
    ):
        """获取点赞列表"""
        echo = self._generate_echo()
        payload = account_payload.GetProfileLikePayload(
            params=account_payload.GetProfileLikeParams(
                user_id=user_id, start=start, count=count
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def nc_get_user_status(self, user_id: int):
        """获取用户状态"""
        echo = self._generate_echo()
        payload = account_payload.NcGetUserStatusPayload(
            params=account_payload.NcGetUserStatusParams(user_id=user_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_unidirectional_friend_list(self):
        """获取单向好友列表"""
        echo = self._generate_echo()
        payload = account_payload.GetUnidirectionalFriendListPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_login_info(self):
        """获取登录号信息"""
        echo = self._generate_echo()
        payload = account_payload.GetLoginInfoPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_status(self):
        """获取状态"""
        echo = self._generate_echo()
        payload = account_payload.GetStatusPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_online_clients(self):
        """获取在线客户端列表"""
        echo = self._generate_echo()
        payload = account_payload.GetOnlineClientsPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def get_model_show(self, model: str):
        """获取在线机型"""
        echo = self._generate_echo()
        payload = account_payload.GetModelShowPayload(
            params=account_payload.GetModelShowParams(model=model),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_model_show(self, model: str, model_show: str) -> None:
        """设置在线机型"""
        payload = account_payload.SetModelShowPayload(
            params=account_payload.SetModelShowParams(
                model=model, model_show=model_show
            )
        )
        await self._send_payload(payload)

    async def get_doubt_friends_add_request(self, count: int = 50):
        """获取被过滤好友请求"""
        echo = self._generate_echo()
        payload = account_payload.GetDoubtFriendsAddRequestPayload(
            params=account_payload.GetDoubtFriendsAddRequestParams(count=count),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_doubt_friends_add_request(self, flag: str, approve: bool) -> None:
        """处理被过滤好友请求"""
        payload = account_payload.SetDoubtFriendsAddRequestPayload(
            params=account_payload.SetDoubtFriendsAddRequestParams(
                flag=flag, approve=approve
            )
        )
        await self._send_payload(payload)

    async def set_friend_remark(self, user_id: int, remark: str) -> None:
        """设置好友备注"""
        payload = account_payload.SetFriendRemarkPayload(
            params=account_payload.SetFriendRemarkParams(user_id=user_id, remark=remark)
        )
        await self._send_payload(payload)
