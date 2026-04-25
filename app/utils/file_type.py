"""文件类型检测工具。"""

from typing import Protocol, cast

import filetype  # pyright: ignore[reportMissingTypeStubs]

from .encoding import base64_to_bytes


class FileKind(Protocol):
    """filetype 返回对象的最小字段集合。"""

    mime: str
    extension: str


def _guess_file_kind(data: bytes) -> FileKind | None:
    """调用无类型存根的 filetype，并在工具边界完成类型收窄。"""
    return cast(FileKind | None, filetype.guess(data))  # pyright: ignore[reportUnknownMemberType]


def detect_mime_type(data: bytes | str) -> str:
    """检测文件 MIME 类型，无法识别时显式报错。"""
    byte_data = base64_to_bytes(data) if isinstance(data, str) else data
    kind = _guess_file_kind(byte_data)
    if kind is None:
        raise ValueError("无法识别的文件格式")
    return kind.mime


def detect_extension(data: bytes) -> str:
    """检测文件扩展名，无法识别时返回 unknown。"""
    kind = _guess_file_kind(data)
    if kind is None:
        return "unknown"
    return "." + kind.extension
