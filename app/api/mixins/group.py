"""群聊相关 Mixin 类"""

from typing import Literal

from app.models.api.payloads import group as group_payload

from .base import BaseMixin


class GroupMixin(BaseMixin):
    """群聊相关的 API 接口"""

    async def send_group_ai_record(
        self,
        group_id: int,
        character: str,
        text: str,
    ) -> None:
        """发送群AI语音"""
        payload = group_payload.GroupAiRecordPayload(
            params=group_payload.GroupAiRecordParams(
                group_id=group_id, character=character, text=text
            )
        )
        await self._send_payload(payload)

    async def get_group_detail_info(self, group_id: int):
        """获取群详细信息"""
        echo = self._generate_echo()
        payload = group_payload.GroupDetailInfoPayload(
            params=group_payload.GroupDetailInfoParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_group_kick(
        self,
        group_id: int,
        user_id: int,
        reject_add_request: bool = False,
    ) -> None:
        """踢出群成员"""
        payload = group_payload.GroupKickPayload(
            params=group_payload.GroupKickParams(
                group_id=group_id,
                user_id=user_id,
                reject_add_request=reject_add_request,
            )
        )
        await self._send_payload(payload)

    async def set_group_ban(
        self,
        group_id: int,
        user_id: int,
        duration: int,
    ) -> None:
        """群禁言"""
        payload = group_payload.GroupBanPayload(
            params=group_payload.GroupBanParameters(
                group_id=group_id,
                user_id=user_id,
                duration=duration,
            )
        )
        await self._send_payload(payload)

    async def get_essence_msg_list(self, group_id: int):
        """获取群精华消息"""
        echo = self._generate_echo()
        payload = group_payload.GetEssenceMsgListPayload(
            params=group_payload.GetEssenceMsgListParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_group_whole_ban(self, group_id: int, enable: bool) -> None:
        """全体禁言"""
        payload = group_payload.SetGroupWholeBanPayload(
            params=group_payload.SetGroupWholeBanParams(
                group_id=group_id, enable=enable
            )
        )
        await self._send_payload(payload)

    async def set_group_portrait(self, group_id: int, file: str) -> None:
        """设置群头像"""
        payload = group_payload.SetGroupPortraitPayload(
            params=group_payload.SetGroupPortraitParams(group_id=group_id, file=file)
        )
        await self._send_payload(payload)

    async def set_group_admin(self, group_id: int, user_id: int, enable: bool) -> None:
        """设置群管理"""
        payload = group_payload.SetGroupAdminPayload(
            params=group_payload.SetGroupAdminParams(
                group_id=group_id, user_id=user_id, enable=enable
            )
        )
        await self._send_payload(payload)

    async def set_group_card(
        self, group_id: int, user_id: int, card: str | None = None
    ) -> None:
        """设置群成员名片"""
        payload = group_payload.SetGroupCardPayload(
            params=group_payload.SetGroupCardParams(
                group_id=group_id, user_id=user_id, card=card
            )
        )
        await self._send_payload(payload)

    async def set_essence_msg(self, message_id: int) -> None:
        """设置群精华消息"""
        payload = group_payload.SetEssenceMsgPayload(
            params=group_payload.SetEssenceMsgParams(message_id=message_id)
        )
        await self._send_payload(payload)

    async def set_group_name(self, group_id: int, group_name: str) -> None:
        """设置群名"""
        payload = group_payload.SetGroupNamePayload(
            params=group_payload.SetGroupNameParams(
                group_id=group_id, group_name=group_name
            )
        )
        await self._send_payload(payload)

    async def delete_essence_msg(self, message_id: int) -> None:
        """删除群精华消息"""
        payload = group_payload.DeleteEssenceMsgPayload(
            params=group_payload.DeleteEssenceMsgParams(message_id=message_id)
        )
        await self._send_payload(payload)

    async def del_group_notice(self, group_id: int, notice_id: str) -> None:
        """删除群公告"""
        payload = group_payload.DelGroupNoticePayload(
            params=group_payload.DelGroupNoticeParams(
                group_id=group_id, notice_id=notice_id
            )
        )
        await self._send_payload(payload)

    async def set_group_leave(
        self, group_id: int, is_dismiss: bool | None = None
    ) -> None:
        """退群"""
        payload = group_payload.SetGroupLeavePayload(
            params=group_payload.SetGroupLeaveParams(
                group_id=group_id, is_dismiss=is_dismiss
            )
        )
        await self._send_payload(payload)

    async def send_group_notice(
        self,
        group_id: int,
        content: str,
        image: str | None = None,
        pinned: int | None = None,
        type: int | None = None,
        confirm_required: int | None = None,
        is_show_edit_card: int | None = None,
        tip_window_type: int | None = None,
    ) -> None:
        """发送群公告"""
        payload = group_payload.SendGroupNoticePayload(
            params=group_payload.SendGroupNoticeParams(
                group_id=group_id,
                content=content,
                image=image,
                pinned=pinned,
                type=type,
                confirm_required=confirm_required,
                is_show_edit_card=is_show_edit_card,
                tip_window_type=tip_window_type,
            )
        )
        await self._send_payload(payload)

    async def set_group_search(
        self,
        group_id: int,
        no_code_finger_open: int | None = None,
        no_finger_open: int | None = None,
    ) -> None:
        """设置群搜索"""
        payload = group_payload.SetGroupSearchPayload(
            params=group_payload.SetGroupSearchParams(
                group_id=group_id,
                no_code_finger_open=no_code_finger_open,
                no_finger_open=no_finger_open,
            )
        )
        await self._send_payload(payload)

    async def get_group_notice(self, group_id: int):
        """获取群公告"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupNoticePayload(
            params=group_payload.GetGroupNoticeParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_group_add_request(
        self, flag: str, approve: bool, reason: str | None = None
    ) -> None:
        """处理加群请求"""
        payload = group_payload.SetGroupAddRequestPayload(
            params=group_payload.SetGroupAddRequestParams(
                flag=flag, approve=approve, reason=reason
            )
        )
        await self._send_payload(payload)

    async def get_group_info(self, group_id: int):
        """获取群信息"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupInfoPayload(
            params=group_payload.GetGroupInfoParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_list(self, no_cache: bool = False):
        """获取群列表"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupListPayload(
            params=group_payload.GetGroupListParams(no_cache=no_cache),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_member_info(
        self, group_id: int, user_id: int, no_cache: bool = False
    ):
        """获取群成员信息"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupMemberInfoPayload(
            params=group_payload.GetGroupMemberInfoParams(
                group_id=group_id, user_id=user_id, no_cache=no_cache
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_member_list(self, group_id: int, no_cache: bool = False):
        """获取群成员列表"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupMemberListPayload(
            params=group_payload.GetGroupMemberListParams(
                group_id=group_id, no_cache=no_cache
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_honor_info(self, group_id: int, type: str = "all"):
        """获取群荣誉"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupHonorInfoPayload(
            params=group_payload.GetGroupHonorInfoParams(group_id=group_id, type=type),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_at_all_remain(self, group_id: int):
        """获取群@全体成员剩余次数"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupAtAllRemainPayload(
            params=group_payload.GetGroupAtAllRemainParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_shut_list(self, group_id: int):
        """获取群禁言列表"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupShutListPayload(
            params=group_payload.GetGroupShutListParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_group_sign(self, group_id: int) -> None:
        """群打卡"""
        payload = group_payload.SetGroupSignPayload(
            params=group_payload.SetGroupSignParams(group_id=str(group_id))
        )
        await self._send_payload(payload)

    async def set_group_todo(
        self, group_id: int, message_id: int, message_seq: str | None = None
    ) -> None:
        """设置群代办"""
        payload = group_payload.SetGroupTodoPayload(
            params=group_payload.SetGroupTodoParams(
                group_id=str(group_id),
                message_id=str(message_id),
                message_seq=message_seq,
            )
        )
        await self._send_payload(payload)

    async def get_ai_characters(self, group_id: int, chat_type: Literal[1, 2] = 1):
        """获取群AI角色列表"""
        echo = self._generate_echo()
        payload = group_payload.AiCharactersPayload(
            params=group_payload.AiCharactersParams(
                group_id=group_id, chat_type=chat_type
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_group_special_title(
        self,
        group_id: int,
        user_id: int,
        special_title: str | None = None,
        duration: int = -1,
    ) -> None:
        """设置群头衔"""
        payload = group_payload.SetGroupSpecialTitlePayload(
            params=group_payload.SetGroupSpecialTitleParams(
                group_id=group_id,
                user_id=user_id,
                special_title=special_title,
                duration=duration,
            )
        )
        await self._send_payload(payload)

    async def get_group_system_msg(self):
        """获取群系统消息"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupSystemMsgPayload(echo=echo)
        return await self._send_and_wait(payload)

    async def set_group_remark(self, group_id: int, remark: str) -> None:
        """设置群备注"""
        payload = group_payload.SetGroupRemarkPayload(
            params=group_payload.SetGroupRemarkParams(group_id=group_id, remark=remark)
        )
        await self._send_payload(payload)

    async def get_group_info_ex(self, group_id: int):
        """获取群信息ex"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupInfoExPayload(
            params=group_payload.GetGroupInfoExParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_ignored_notifies(self, group_id: int):
        """获取群过滤系统消息"""
        echo = self._generate_echo()
        payload = group_payload.GetGroupIgnoredNotifiesPayload(
            params=group_payload.GetGroupIgnoredNotifiesParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def set_group_kick_members(
        self, group_id: int, user_ids: list[int], reject_add_request: bool = False
    ) -> None:
        """批量踢出群成员"""
        payload = group_payload.SetGroupKickMembersPayload(
            params=group_payload.SetGroupKickMembersParams(
                group_id=group_id,
                user_ids=user_ids,
                reject_add_request=reject_add_request,
            )
        )
        await self._send_payload(payload)
