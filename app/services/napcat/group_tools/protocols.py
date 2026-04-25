"""NapCat 群聊工具集依赖协议。"""

from typing import Protocol

from app.models import (
    GroupMessage,
    MessageSegment,
    Meta,
    NapCatId,
    Notice,
    PrivateMessage,
    Request,
    Response,
)

type CachedNapCatMessage = GroupMessage | PrivateMessage | Notice | Meta | Request


class NapCatGroupToolBot(Protocol):
    """描述群聊本地工具所需的最小 NapCat BOT 能力。"""

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


class NapCatGroupHistoryDatabase(Protocol):
    """描述群聊历史工具读取 Redis 缓存所需的最小数据库能力。"""

    async def search_messages(
        self,
        *,
        self_id: NapCatId,
        message_id: NapCatId | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: NapCatId | None = None,
        user_id: NapCatId | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> CachedNapCatMessage | list[CachedNapCatMessage] | None:
        """查询 Redis 中的消息缓存。"""
        ...
