"""文件相关 Payload 模型"""

from typing import Literal

from pydantic import BaseModel


# ==================== 上传群文件 ====================
class UploadGroupFileParams(BaseModel):
    group_id: int
    file: str
    name: str
    folder: str | None = None
    folder_id: str | None = None


class UploadGroupFilePayload(BaseModel):
    """上传群文件"""

    action: Literal["upload_group_file"] = "upload_group_file"
    params: UploadGroupFileParams


# ==================== 上传私聊文件 ====================
class UploadPrivateFileParams(BaseModel):
    user_id: int
    file: str
    name: str


class UploadPrivateFilePayload(BaseModel):
    """上传私聊文件"""

    action: Literal["upload_private_file"] = "upload_private_file"
    params: UploadPrivateFileParams


# ==================== 获取群根目录文件列表 ====================
class GetGroupRootFilesParams(BaseModel):
    group_id: int
    file_count: int = 50


class GetGroupRootFilesPayload(BaseModel):
    """获取群根目录文件列表"""

    action: Literal["get_group_root_files"] = "get_group_root_files"
    params: GetGroupRootFilesParams
    echo: str


# ==================== 获取群子目录文件列表 ====================
class GetGroupFilesByFolderParams(BaseModel):
    group_id: int
    folder_id: str | None = None
    folder: str | None = None
    file_count: int = 50


class GetGroupFilesByFolderPayload(BaseModel):
    """获取群子目录文件列表"""

    action: Literal["get_group_files_by_folder"] = "get_group_files_by_folder"
    params: GetGroupFilesByFolderParams
    echo: str


# ==================== 获取群文件系统信息 ====================
class GetGroupFileSystemInfoParams(BaseModel):
    group_id: int


class GetGroupFileSystemInfoPayload(BaseModel):
    """获取群文件系统信息"""

    action: Literal["get_group_file_system_info"] = "get_group_file_system_info"
    params: GetGroupFileSystemInfoParams
    echo: str


# ==================== 获取文件信息 ====================
class GetFileParams(BaseModel):
    file_id: str | None = None
    file: str | None = None


class GetFilePayload(BaseModel):
    """获取文件信息"""

    action: Literal["get_file"] = "get_file"
    params: GetFileParams
    echo: str


# ==================== 获取群文件链接 ====================
class GetGroupFileUrlParams(BaseModel):
    group_id: int
    file_id: str


class GetGroupFileUrlPayload(BaseModel):
    """获取群文件链接"""

    action: Literal["get_group_file_url"] = "get_group_file_url"
    params: GetGroupFileUrlParams
    echo: str


# ==================== 获取私聊文件链接 ====================
class GetPrivateFileUrlParams(BaseModel):
    file_id: str


class GetPrivateFileUrlPayload(BaseModel):
    """获取私聊文件链接"""

    action: Literal["get_private_file_url"] = "get_private_file_url"
    params: GetPrivateFileUrlParams
    echo: str


# ==================== 创建群文件文件夹 ====================
class CreateGroupFileFolderParams(BaseModel):
    group_id: int
    folder_name: str


class CreateGroupFileFolderPayload(BaseModel):
    """创建群文件文件夹"""

    action: Literal["create_group_file_folder"] = "create_group_file_folder"
    params: CreateGroupFileFolderParams
    echo: str


# ==================== 删除群文件 ====================
class DeleteGroupFileParams(BaseModel):
    group_id: int
    file_id: str


class DeleteGroupFilePayload(BaseModel):
    """删除群文件"""

    action: Literal["delete_group_file"] = "delete_group_file"
    params: DeleteGroupFileParams
    echo: str


# ==================== 删除群文件夹 ====================
class DeleteGroupFolderParams(BaseModel):
    group_id: int
    folder_id: str


class DeleteGroupFolderPayload(BaseModel):
    """删除群文件夹"""

    action: Literal["delete_group_folder"] = "delete_group_folder"
    params: DeleteGroupFolderParams
    echo: str


# ==================== 移动群文件 ====================
class MoveGroupFileParams(BaseModel):
    group_id: int
    file_id: str
    current_parent_directory: str
    target_parent_directory: str


class MoveGroupFilePayload(BaseModel):
    """移动群文件"""

    action: Literal["move_group_file"] = "move_group_file"
    params: MoveGroupFileParams
    echo: str


# ==================== 重命名群文件 ====================
class RenameGroupFileParams(BaseModel):
    group_id: int
    file_id: str
    current_parent_directory: str
    new_name: str


class RenameGroupFilePayload(BaseModel):
    """重命名群文件"""

    action: Literal["rename_group_file"] = "rename_group_file"
    params: RenameGroupFileParams
    echo: str


# ==================== 下载文件到缓存目录 ====================
class DownloadFileParams(BaseModel):
    url: str | None = None
    base64: str | None = None
    name: str | None = None
    headers: str | list[str] | None = None
    thread_cnt: int | None = None


class DownloadFilePayload(BaseModel):
    """下载文件到缓存目录"""

    action: Literal["download_file"] = "download_file"
    params: DownloadFileParams
    echo: str


# ==================== 转存为永久文件 ====================
class TransGroupFileParams(BaseModel):
    group_id: int
    file_id: str


class TransGroupFilePayload(BaseModel):
    """转存为永久文件"""

    action: Literal["trans_group_file"] = "trans_group_file"
    params: TransGroupFileParams
    echo: str


# ==================== 清空缓存 ====================
class CleanCachePayload(BaseModel):
    """清空缓存"""

    action: Literal["clean_cache"] = "clean_cache"
    echo: str
