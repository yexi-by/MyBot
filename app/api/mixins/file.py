"""NapCat 文件相关 Action。"""

from app.models import NapCatId, Response

from .base import BaseMixin


class FileMixin(BaseMixin):
    """文件相关 API。"""

    async def upload_group_file(
        self,
        group_id: NapCatId,
        file: str,
        name: str,
        folder: str | None = None,
        folder_id: str | None = None,
    ) -> None:
        """上传群文件。"""
        await self._send_action(
            "upload_group_file",
            self._build_params(
                group_id=group_id,
                file=file,
                name=name,
                folder=folder,
                folder_id=folder_id,
            ),
        )

    async def upload_private_file(
        self, user_id: NapCatId, file: str, name: str
    ) -> None:
        """上传私聊文件。"""
        await self._send_action(
            "upload_private_file",
            self._build_params(user_id=user_id, file=file, name=name),
        )

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

    async def get_group_file_system_info(self, group_id: NapCatId) -> Response:
        """获取群文件系统信息。"""
        return await self._call_action(
            "get_group_file_system_info", self._build_params(group_id=group_id)
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

    async def get_private_file_url(self, file_id: str) -> Response:
        """获取私聊文件链接。"""
        return await self._call_action(
            "get_private_file_url", self._build_params(file_id=file_id)
        )

    async def create_group_file_folder(
        self, group_id: NapCatId, folder_name: str
    ) -> Response:
        """创建群文件夹。"""
        return await self._call_action(
            "create_group_file_folder",
            self._build_params(group_id=group_id, folder_name=folder_name),
        )

    async def delete_group_file(self, group_id: NapCatId, file_id: str) -> Response:
        """删除群文件。"""
        return await self._call_action(
            "delete_group_file",
            self._build_params(group_id=group_id, file_id=file_id),
        )

    async def delete_group_folder(self, group_id: NapCatId, folder_id: str) -> Response:
        """删除群文件夹。"""
        return await self._call_action(
            "delete_group_folder",
            self._build_params(group_id=group_id, folder_id=folder_id),
        )

    async def move_group_file(
        self,
        group_id: NapCatId,
        file_id: str,
        current_parent_directory: str,
        target_parent_directory: str,
    ) -> Response:
        """移动群文件。"""
        return await self._call_action(
            "move_group_file",
            self._build_params(
                group_id=group_id,
                file_id=file_id,
                current_parent_directory=current_parent_directory,
                target_parent_directory=target_parent_directory,
            ),
        )

    async def rename_group_file(
        self,
        group_id: NapCatId,
        file_id: str,
        current_parent_directory: str,
        new_name: str,
    ) -> Response:
        """重命名群文件。"""
        return await self._call_action(
            "rename_group_file",
            self._build_params(
                group_id=group_id,
                file_id=file_id,
                current_parent_directory=current_parent_directory,
                new_name=new_name,
            ),
        )

    async def download_file(
        self,
        url: str | None = None,
        base64: str | None = None,
        name: str | None = None,
        headers: str | list[str] | None = None,
        thread_cnt: int | None = None,
    ) -> Response:
        """下载文件到缓存目录。"""
        return await self._call_action(
            "download_file",
            self._build_params(
                url=url,
                base64=base64,
                name=name,
                headers=headers,
                thread_cnt=thread_cnt,
            ),
        )

    async def trans_group_file(self, group_id: NapCatId, file_id: str) -> Response:
        """转存群文件。"""
        return await self._call_action(
            "trans_group_file",
            self._build_params(group_id=group_id, file_id=file_id),
        )

    async def clean_cache(self) -> Response:
        """清空缓存。"""
        return await self._call_action("clean_cache")
