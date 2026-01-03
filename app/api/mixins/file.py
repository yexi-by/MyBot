"""文件相关 Mixin 类"""

from app.models.api.payloads import file as file_payload
from app.models.events.response import Response

from .base import BaseMixin


class FileMixin(BaseMixin):
    """文件相关的 API 接口"""

    async def upload_group_file(
        self,
        group_id: int,
        file: str,
        name: str,
        folder: str | None = None,
        folder_id: str | None = None,
    ) -> None:
        """上传群文件"""
        payload = file_payload.UploadGroupFilePayload(
            params=file_payload.UploadGroupFileParams(
                group_id=group_id,
                file=file,
                name=name,
                folder=folder,
                folder_id=folder_id,
            )
        )
        await self._send_payload(payload)

    async def upload_private_file(
        self,
        user_id: int,
        file: str,
        name: str,
    ) -> None:
        """上传私聊文件"""
        payload = file_payload.UploadPrivateFilePayload(
            params=file_payload.UploadPrivateFileParams(
                user_id=user_id,
                file=file,
                name=name,
            )
        )
        await self._send_payload(payload)

    async def get_group_root_files(
        self, group_id: int, file_count: int = 50
    ) -> Response:
        """获取群根目录文件列表"""
        echo = self._generate_echo()
        payload = file_payload.GetGroupRootFilesPayload(
            params=file_payload.GetGroupRootFilesParams(
                group_id=group_id, file_count=file_count
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_files_by_folder(
        self,
        group_id: int,
        folder_id: str | None = None,
        folder: str | None = None,
        file_count: int = 50,
    ) -> Response:
        """获取群子目录文件列表"""
        echo = self._generate_echo()
        payload = file_payload.GetGroupFilesByFolderPayload(
            params=file_payload.GetGroupFilesByFolderParams(
                group_id=group_id,
                folder_id=folder_id,
                folder=folder,
                file_count=file_count,
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_file_system_info(self, group_id: int) -> Response:
        """获取群文件系统信息"""
        echo = self._generate_echo()
        payload = file_payload.GetGroupFileSystemInfoPayload(
            params=file_payload.GetGroupFileSystemInfoParams(group_id=group_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_file(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """获取文件信息"""
        echo = self._generate_echo()
        payload = file_payload.GetFilePayload(
            params=file_payload.GetFileParams(file_id=file_id, file=file),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_group_file_url(self, group_id: int, file_id: str) -> Response:
        """获取群文件链接"""
        echo = self._generate_echo()
        payload = file_payload.GetGroupFileUrlPayload(
            params=file_payload.GetGroupFileUrlParams(
                group_id=group_id, file_id=file_id
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def get_private_file_url(self, file_id: str) -> Response:
        """获取私聊文件链接"""
        echo = self._generate_echo()
        payload = file_payload.GetPrivateFileUrlPayload(
            params=file_payload.GetPrivateFileUrlParams(file_id=file_id),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def create_group_file_folder(
        self, group_id: int, folder_name: str
    ) -> Response:
        """创建群文件文件夹"""
        echo = self._generate_echo()
        payload = file_payload.CreateGroupFileFolderPayload(
            params=file_payload.CreateGroupFileFolderParams(
                group_id=group_id, folder_name=folder_name
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def delete_group_file(self, group_id: int, file_id: str) -> Response:
        """删除群文件"""
        echo = self._generate_echo()
        payload = file_payload.DeleteGroupFilePayload(
            params=file_payload.DeleteGroupFileParams(
                group_id=group_id, file_id=file_id
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def delete_group_folder(self, group_id: int, folder_id: str) -> Response:
        """删除群文件夹"""
        echo = self._generate_echo()
        payload = file_payload.DeleteGroupFolderPayload(
            params=file_payload.DeleteGroupFolderParams(
                group_id=group_id, folder_id=folder_id
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def move_group_file(
        self,
        group_id: int,
        file_id: str,
        current_parent_directory: str,
        target_parent_directory: str,
    ) -> Response:
        """移动群文件"""
        echo = self._generate_echo()
        payload = file_payload.MoveGroupFilePayload(
            params=file_payload.MoveGroupFileParams(
                group_id=group_id,
                file_id=file_id,
                current_parent_directory=current_parent_directory,
                target_parent_directory=target_parent_directory,
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def rename_group_file(
        self,
        group_id: int,
        file_id: str,
        current_parent_directory: str,
        new_name: str,
    ) -> Response:
        """重命名群文件"""
        echo = self._generate_echo()
        payload = file_payload.RenameGroupFilePayload(
            params=file_payload.RenameGroupFileParams(
                group_id=group_id,
                file_id=file_id,
                current_parent_directory=current_parent_directory,
                new_name=new_name,
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def download_file(
        self,
        url: str | None = None,
        base64: str | None = None,
        name: str | None = None,
        headers: str | list[str] | None = None,
        thread_cnt: int | None = None,
    ) -> Response:
        """下载文件到缓存目录"""
        echo = self._generate_echo()
        payload = file_payload.DownloadFilePayload(
            params=file_payload.DownloadFileParams(
                url=url,
                base64=base64,
                name=name,
                headers=headers,
                thread_cnt=thread_cnt,
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def trans_group_file(self, group_id: int, file_id: str) -> Response:
        """转存为永久文件"""
        echo = self._generate_echo()
        payload = file_payload.TransGroupFilePayload(
            params=file_payload.TransGroupFileParams(
                group_id=group_id, file_id=file_id
            ),
            echo=echo,
        )
        return await self._send_and_wait(payload)

    async def clean_cache(self) -> Response:
        """清空缓存"""
        echo = self._generate_echo()
        payload = file_payload.CleanCachePayload(echo=echo)
        return await self._send_and_wait(payload)
