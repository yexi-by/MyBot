"""流式操作 Payload 模型"""

from typing import Literal

from pydantic import BaseModel


# ==================== 清理流临时文件 ====================
class CleanStreamTempFileParams(BaseModel):
    """清理流临时文件参数"""

    pass


class CleanStreamTempFilePayload(BaseModel):
    """清理流临时文件"""

    action: Literal["clean_stream_temp_file"] = "clean_stream_temp_file"
    params: CleanStreamTempFileParams
    echo: str


# ==================== 流式下载测试 ====================
class TestDownloadStreamParams(BaseModel):
    """流式下载测试参数"""

    error: bool = False


class TestDownloadStreamPayload(BaseModel):
    """流式下载测试"""

    action: Literal["test_download_stream"] = "test_download_stream"
    params: TestDownloadStreamParams
    echo: str


# ==================== 流式上传文件 ====================
class UploadFileStreamParams(BaseModel):
    """流式上传文件参数"""

    stream_id: str
    chunk_data: str | None = None
    chunk_index: int | None = None
    total_chunks: int | None = None
    file_size: int | None = None
    expected_sha256: str | None = None
    is_complete: bool | None = None
    filename: str | None = None
    reset: bool | None = None
    verify_only: bool | None = None
    file_retention: int = 300000


class UploadFileStreamPayload(BaseModel):
    """流式上传文件"""

    action: Literal["upload_file_stream"] = "upload_file_stream"
    params: UploadFileStreamParams
    echo: str


# ==================== 流式下载文件 ====================
class DownloadFileStreamParams(BaseModel):
    """流式下载文件参数"""

    file: str | None = None
    file_id: str | None = None
    chunk_size: int = 65536


class DownloadFileStreamPayload(BaseModel):
    """流式下载文件"""

    action: Literal["download_file_stream"] = "download_file_stream"
    params: DownloadFileStreamParams
    echo: str


# ==================== 流式下载语音文件 ====================
class DownloadFileRecordStreamParams(BaseModel):
    """流式下载语音文件参数"""

    file: str | None = None
    file_id: str | None = None
    chunk_size: int = 65536
    out_format: str | None = None


class DownloadFileRecordStreamPayload(BaseModel):
    """流式下载语音文件"""

    action: Literal["download_file_record_stream"] = "download_file_record_stream"
    params: DownloadFileRecordStreamParams
    echo: str


# ==================== 流式下载图片 ====================
class DownloadFileImageStreamParams(BaseModel):
    """流式下载图片参数"""

    file: str | None = None
    file_id: str | None = None
    chunk_size: int = 65536


class DownloadFileImageStreamPayload(BaseModel):
    """流式下载图片"""

    action: Literal["download_file_image_stream"] = "download_file_image_stream"
    params: DownloadFileImageStreamParams
    echo: str
