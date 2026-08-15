"""NapCat 群聊工具集依赖协议。"""

from typing import Protocol

from app.models import MessageSegment, NapCatId, Node, Response
from app.services.napcat.image_reader import NapCatImageBot


class NapCatGroupToolBot(NapCatImageBot, Protocol):
    """描述群聊本地工具所需的最小 NapCat BOT 能力。"""

    boot_id: NapCatId

    async def send_msg(
        self,
        *,
        group_id: NapCatId,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """发送群消息段。"""
        ...

    async def get_group_root_files(
        self, group_id: NapCatId, file_count: int = 50
    ) -> Response:
        """获取群文件根目录。"""
        ...

    async def get_group_files_by_folder(
        self,
        group_id: NapCatId,
        folder_id: str | None = None,
        folder: str | None = None,
        file_count: int = 50,
    ) -> Response:
        """获取群文件子目录。"""
        ...

    async def get_group_file_url(self, group_id: NapCatId, file_id: str) -> Response:
        """获取群文件下载链接。"""
        ...

    async def get_forward_msg(self, message_id: NapCatId) -> Response:
        """获取合并转发消息详情。"""
        ...

    async def send_group_forward_msg(
        self, *, group_id: NapCatId, messages: list[Node]
    ) -> Response:
        """发送群聊合并转发消息。"""
        ...
