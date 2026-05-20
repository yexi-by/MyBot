"""NapCat 文件相关 Action。"""

import asyncio
import base64 as base64_lib
import hashlib
import uuid
from pathlib import Path

import aiofiles

from app.models import NapCatId, Response, StreamTransferResult

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
        upload_file: bool = True,
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
                upload_file=upload_file,
            ),
        )

    async def upload_private_file(
        self, user_id: NapCatId, file: str, name: str, upload_file: bool = True
    ) -> None:
        """上传私聊文件。"""
        await self._send_action(
            "upload_private_file",
            self._build_params(
                user_id=user_id, file=file, name=name, upload_file=upload_file
            ),
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
    ) -> Response:
        """下载文件到缓存目录。"""
        return await self._call_action(
            "download_file",
            self._build_params(
                url=url,
                base64=base64,
                name=name,
                headers=headers,
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

    async def clean_stream_temp_file(self) -> Response:
        """清理 Stream API 临时文件。"""
        return await self._call_action("clean_stream_temp_file")

    async def test_download_stream(
        self, error: bool = False
    ) -> StreamTransferResult:
        """测试 NapCat 下载流响应聚合。"""
        return await self._call_stream_action(
            "test_download_stream", self._build_params(error=error)
        )

    async def download_file_stream(
        self,
        file: str | None = None,
        file_id: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> StreamTransferResult:
        """通过 Stream API 下载普通文件。"""
        return await self._call_stream_action(
            "download_file_stream",
            self._build_params(
                file=file, file_id=file_id, chunk_size=chunk_size
            ),
        )

    async def download_file_image_stream(
        self,
        file: str | None = None,
        file_id: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> StreamTransferResult:
        """通过 Stream API 下载图片文件。"""
        return await self._call_stream_action(
            "download_file_image_stream",
            self._build_params(
                file=file, file_id=file_id, chunk_size=chunk_size
            ),
        )

    async def download_file_record_stream(
        self,
        file: str | None = None,
        file_id: str | None = None,
        out_format: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> StreamTransferResult:
        """通过 Stream API 下载语音文件。"""
        return await self._call_stream_action(
            "download_file_record_stream",
            self._build_params(
                file=file,
                file_id=file_id,
                out_format=out_format,
                chunk_size=chunk_size,
            ),
        )

    async def upload_file_stream(
        self,
        stream_id: str,
        *,
        chunk_data: str | None = None,
        chunk_index: int | None = None,
        total_chunks: int | None = None,
        file_size: int | None = None,
        expected_sha256: str | None = None,
        is_complete: bool | None = None,
        filename: str | None = None,
        reset: bool | None = None,
        verify_only: bool | None = None,
        file_retention: int = 300000,
    ) -> Response:
        """上传 Stream API 文件分片或完成信号。"""
        return await self._call_action(
            "upload_file_stream",
            self._build_params(
                stream_id=stream_id,
                chunk_data=chunk_data,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                file_size=file_size,
                expected_sha256=expected_sha256,
                is_complete=is_complete,
                filename=filename,
                reset=reset,
                verify_only=verify_only,
                file_retention=file_retention,
            ),
        )

    async def upload_local_file_stream(
        self,
        path: Path,
        chunk_size: int = 64 * 1024,
        file_retention: int = 300000,
    ) -> Response:
        """读取本地文件并通过 Stream API 分片上传。"""
        if chunk_size <= 0:
            raise ValueError("Stream 上传分片大小必须大于 0")
        if file_retention <= 0:
            raise ValueError("Stream 上传文件保留时间必须大于 0")
        if not await asyncio.to_thread(path.is_file):
            raise ValueError(f"Stream 上传文件不存在: {path}")

        file_size, expected_sha256 = await self._calculate_stream_file_digest(
            path=path, chunk_size=chunk_size
        )
        if file_size == 0:
            raise ValueError("Stream 上传文件不能为空")

        stream_id = str(uuid.uuid4())
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        chunk_index = 0
        async with aiofiles.open(path, mode="rb") as file:
            while True:
                raw_chunk = await file.read(chunk_size)
                if raw_chunk == b"":
                    break
                chunk_data = base64_lib.b64encode(raw_chunk).decode("ascii")
                _ = await self.upload_file_stream(
                    stream_id=stream_id,
                    chunk_data=chunk_data,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    file_size=file_size,
                    expected_sha256=expected_sha256,
                    filename=path.name,
                    file_retention=file_retention,
                )
                chunk_index += 1

        return await self.upload_file_stream(
            stream_id=stream_id,
            is_complete=True,
            file_retention=file_retention,
        )

    async def _calculate_stream_file_digest(
        self, *, path: Path, chunk_size: int
    ) -> tuple[int, str]:
        """异步计算 Stream 上传文件大小与 SHA256。"""
        digest = hashlib.sha256()
        file_size = 0
        async with aiofiles.open(path, mode="rb") as file:
            while True:
                raw_chunk = await file.read(chunk_size)
                if raw_chunk == b"":
                    break
                file_size += len(raw_chunk)
                digest.update(raw_chunk)
        return file_size, digest.hexdigest()
