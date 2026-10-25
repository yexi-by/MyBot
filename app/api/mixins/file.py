"""NapCat 文件相关 Action。"""

from app.models import NapCatId, Response

from .base import BaseMixin


class FileMixin(BaseMixin):
    """文件相关 API。"""

    async def get_group_root_files(
        self, group_id: NapCatId, file_count: int = 50
    ) -> Response:
        """获取群根目录文件列表。"""
        return await self._call_action(
            "get_group_root_files",
            self._build_params(group_id=group_id, file_count=file_count),
        )

    async def get_group_files_by_folder(
        self,
        group_id: NapCatId,
        folder_id: str | None = None,
        folder: str | None = None,
        file_count: int = 50,
    ) -> Response:
        """获取群子目录文件列表。"""
        return await self._call_action(
            "get_group_files_by_folder",
            self._build_params(
                group_id=group_id,
                folder_id=folder_id,
                folder=folder,
                file_count=file_count,
            ),
        )

    async def get_file(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """获取文件信息。"""
        return await self._call_action(
            "get_file", self._build_params(file_id=file_id, file=file)
        )

    async def get_group_file_url(self, group_id: NapCatId, file_id: str) -> Response:
        """获取群文件链接。"""
        return await self._call_action(
            "get_group_file_url",
            self._build_params(group_id=group_id, file_id=file_id),
        )
