"""NapCat 群聊相关 Action。"""

from typing import Literal

from app.models import NapCatId, Response

from .base import BaseMixin


class GroupMixin(BaseMixin):
    """群聊相关 API。"""

    async def send_group_ai_record(
        self, group_id: NapCatId, character: str, text: str
    ) -> None:
        """发送群 AI 语音。"""
        await self._send_action(
            "send_group_ai_record",
            self._build_params(group_id=group_id, character=character, text=text),
        )

    async def get_group_detail_info(self, group_id: NapCatId) -> Response:
        """获取群详细信息。"""
        return await self._call_action(
            "get_group_detail_info", self._build_params(group_id=group_id)
        )

    async def set_group_kick(
        self,
        group_id: NapCatId,
        user_id: NapCatId,
        reject_add_request: bool = False,
    ) -> None:
        """踢出群成员。"""
        await self._send_action(
            "set_group_kick",
            self._build_params(
                group_id=group_id,
                user_id=user_id,
                reject_add_request=reject_add_request,
            ),
        )

    async def set_group_ban(
        self, group_id: NapCatId, user_id: NapCatId, duration: int
    ) -> None:
        """群禁言。"""
        await self._send_action(
            "set_group_ban",
            self._build_params(group_id=group_id, user_id=user_id, duration=duration),
        )

    async def get_essence_msg_list(self, group_id: NapCatId) -> Response:
        """获取群精华消息列表。"""
        return await self._call_action(
            "get_essence_msg_list", self._build_params(group_id=group_id)
        )

    async def set_group_whole_ban(self, group_id: NapCatId, enable: bool) -> None:
        """设置全体禁言。"""
        await self._send_action(
            "set_group_whole_ban",
            self._build_params(group_id=group_id, enable=enable),
        )

    async def set_group_portrait(self, group_id: NapCatId, file: str) -> None:
        """设置群头像。"""
        await self._send_action(
            "set_group_portrait", self._build_params(group_id=group_id, file=file)
        )

    async def set_group_admin(
        self, group_id: NapCatId, user_id: NapCatId, enable: bool
    ) -> None:
        """设置群管理员。"""
        await self._send_action(
            "set_group_admin",
            self._build_params(group_id=group_id, user_id=user_id, enable=enable),
        )

    async def set_group_card(
        self, group_id: NapCatId, user_id: NapCatId, card: str | None = None
    ) -> None:
        """设置群成员名片。"""
        await self._send_action(
            "set_group_card",
            self._build_params(group_id=group_id, user_id=user_id, card=card),
        )

    async def set_essence_msg(self, message_id: NapCatId) -> None:
        """设置群精华消息。"""
        await self._send_action(
            "set_essence_msg", self._build_params(message_id=message_id)
        )

    async def delete_essence_msg(self, message_id: NapCatId) -> None:
        """删除群精华消息。"""
        await self._send_action(
            "delete_essence_msg", self._build_params(message_id=message_id)
        )

    async def set_group_name(self, group_id: NapCatId, group_name: str) -> None:
        """设置群名。"""
        await self._send_action(
            "set_group_name",
            self._build_params(group_id=group_id, group_name=group_name),
        )

    async def del_group_notice(self, group_id: NapCatId, notice_id: str) -> None:
        """删除群公告。"""
        await self._send_action(
            "_del_group_notice",
            self._build_params(group_id=group_id, notice_id=notice_id),
        )

    async def set_group_leave(
        self, group_id: NapCatId, is_dismiss: bool | None = None
    ) -> None:
        """退出群聊。"""
        await self._send_action(
            "set_group_leave",
            self._build_params(group_id=group_id, is_dismiss=is_dismiss),
        )

    async def send_group_notice(
        self,
        group_id: NapCatId,
        content: str,
        image: str | None = None,
        pinned: int | None = None,
        type: int | None = None,
        confirm_required: int | None = None,
        is_show_edit_card: int | None = None,
        tip_window_type: int | None = None,
    ) -> None:
        """发送群公告。"""
        await self._send_action(
            "_send_group_notice",
            self._build_params(
                group_id=group_id,
                content=content,
                image=image,
                pinned=pinned,
                type=type,
                confirm_required=confirm_required,
                is_show_edit_card=is_show_edit_card,
                tip_window_type=tip_window_type,
            ),
        )

    async def get_group_notice(self, group_id: NapCatId) -> Response:
        """获取群公告。"""
        return await self._call_action(
            "_get_group_notice", self._build_params(group_id=group_id)
        )

    async def set_group_search(
        self,
        group_id: NapCatId,
        no_code_finger_open: int | None = None,
        no_finger_open: int | None = None,
    ) -> None:
        """设置群搜索。"""
        await self._send_action(
            "set_group_search",
            self._build_params(
                group_id=group_id,
                no_code_finger_open=no_code_finger_open,
                no_finger_open=no_finger_open,
            ),
        )

    async def set_group_add_request(
        self, flag: str, approve: bool, reason: str | None = None
    ) -> None:
        """处理加群请求。"""
        await self._send_action(
            "set_group_add_request",
            self._build_params(flag=flag, approve=approve, reason=reason),
        )

    async def get_group_info(self, group_id: NapCatId) -> Response:
        """获取群信息。"""
        return await self._call_action(
            "get_group_info", self._build_params(group_id=group_id)
        )

    async def get_group_list(self, no_cache: bool = False) -> Response:
        """获取群列表。"""
        return await self._call_action(
            "get_group_list", self._build_params(no_cache=no_cache)
        )

    async def get_group_member_info(
        self, group_id: NapCatId, user_id: NapCatId, no_cache: bool = False
    ) -> Response:
        """获取群成员信息。"""
        return await self._call_action(
            "get_group_member_info",
            self._build_params(group_id=group_id, user_id=user_id, no_cache=no_cache),
        )

    async def get_group_member_list(
        self, group_id: NapCatId, no_cache: bool = False
    ) -> Response:
        """获取群成员列表。"""
        return await self._call_action(
            "get_group_member_list",
            self._build_params(group_id=group_id, no_cache=no_cache),
        )

    async def get_group_honor_info(
        self,
        group_id: NapCatId,
        type: Literal[
            "all", "talkative", "performer", "legend", "strong_newbie", "emotion"
        ] = "all",
    ) -> Response:
        """获取群荣誉信息。"""
        return await self._call_action(
            "get_group_honor_info",
            self._build_params(group_id=group_id, type=type),
        )

    async def get_group_at_all_remain(self, group_id: NapCatId) -> Response:
        """获取群 @全体 剩余次数。"""
        return await self._call_action(
            "get_group_at_all_remain", self._build_params(group_id=group_id)
        )

    async def get_group_shut_list(self, group_id: NapCatId) -> Response:
        """获取群禁言列表。"""
        return await self._call_action(
            "get_group_shut_list", self._build_params(group_id=group_id)
        )

    async def set_group_sign(self, group_id: NapCatId) -> None:
        """群打卡。"""
        await self._send_action("set_group_sign", self._build_params(group_id=group_id))

    async def set_group_todo(
        self,
        group_id: NapCatId,
        message_id: NapCatId,
        message_seq: NapCatId | None = None,
    ) -> None:
        """设置群待办。"""
        await self._send_action(
            "set_group_todo",
            self._build_params(
                group_id=group_id, message_id=message_id, message_seq=message_seq
            ),
        )

    async def get_ai_characters(
        self, group_id: NapCatId, chat_type: Literal[1, 2] = 1
    ) -> Response:
        """获取群 AI 角色列表。"""
        return await self._call_action(
            "get_ai_characters",
            self._build_params(group_id=group_id, chat_type=chat_type),
        )

    async def set_group_special_title(
        self,
        group_id: NapCatId,
        user_id: NapCatId,
        special_title: str | None = None,
        duration: int = -1,
    ) -> None:
        """设置群头衔。"""
        await self._send_action(
            "set_group_special_title",
            self._build_params(
                group_id=group_id,
                user_id=user_id,
                special_title=special_title,
                duration=duration,
            ),
        )

    async def get_group_system_msg(self) -> Response:
        """获取群系统消息。"""
        return await self._call_action("get_group_system_msg")

    async def set_group_remark(self, group_id: NapCatId, remark: str) -> None:
        """设置群备注。"""
        await self._send_action(
            "set_group_remark", self._build_params(group_id=group_id, remark=remark)
        )

    async def get_group_info_ex(self, group_id: NapCatId) -> Response:
        """获取扩展群信息。"""
        return await self._call_action(
            "get_group_info_ex", self._build_params(group_id=group_id)
        )

    async def get_group_ignored_notifies(self, group_id: NapCatId) -> Response:
        """获取群过滤系统消息。"""
        return await self._call_action(
            "get_group_ignored_notifies", self._build_params(group_id=group_id)
        )

    async def set_group_kick_members(
        self,
        group_id: NapCatId,
        user_ids: list[NapCatId],
        reject_add_request: bool = False,
    ) -> None:
        """批量踢出群成员。"""
        await self._send_action(
            "set_group_kick_members",
            self._build_params(
                group_id=group_id,
                user_ids=user_ids,
                reject_add_request=reject_add_request,
            ),
        )
