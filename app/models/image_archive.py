"""图片归档层与数据库之间共用的纯数据对象。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredImage:
    """内容寻址图片的持久化元数据。"""

    storage_key: str
    mime_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        """防止数据库将绝对路径或越界路径当成存储键。"""
        key_path = Path(self.storage_key)
        if key_path.is_absolute() or ".." in key_path.parts:
            raise ValueError("storage_key 必须是图片根目录下的相对路径")
        if self.size_bytes < 1:
            raise ValueError("size_bytes 必须大于等于 1")


@dataclass(frozen=True, slots=True)
class ImageArchiveTask:
    """由任务仓库原子认领的单张图片任务。"""

    task_id: int
    lease_token: str
    attempt_number: int
    label: str
    file: str | None = None
    file_id: str | None = None
    path: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        """检查仓库与 worker 之间的租约协议。"""
        if self.task_id < 1:
            raise ValueError("task_id 必须大于等于 1")
        if self.lease_token.strip() == "":
            raise ValueError("lease_token 不能为空")
        if not 1 <= self.attempt_number <= 4:
            raise ValueError("attempt_number 必须介于 1 和 4 之间")
        if self.label.strip() == "":
            raise ValueError("label 不能为空")


__all__ = ["ImageArchiveTask", "StoredImage"]
