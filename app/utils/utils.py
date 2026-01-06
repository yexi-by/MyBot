import aiofiles
from typing import Any
from pathlib import Path
import json
import base64
import filetype
import httpx


async def write_to_file(
    data: dict[str, Any], directory_path: str | Path | None = None
) -> None:
    if not directory_path:
        directory_path = Path.cwd()
    path = Path(directory_path) / "debug.jsonl"
    async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False) + "\n")


def base64_to_bytes(data: str) -> bytes:
    """base64转字节"""
    if "," in data:
        _, data = data.split(",", 1)
    return base64.b64decode(data)


async def load_text_file(file_path: str | Path) -> str:
    """异步加载文本文件内容"""
    path = Path(file_path)
    async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
        content = await f.read()
    return content


def load_text_file_sync(file_path: str | Path) -> str:
    """同步加载文本文件内容"""
    path = Path(file_path)
    with open(path, mode="r", encoding="utf-8") as f:
        content = f.read()
    return content


def detect_image_mime_type(data: bytes | str) -> str:
    """
    检测图片格式并返回MIME类型

    使用 filetype 库通过文件头魔数判断图片类型

    Args:
        data: 图片的字节数据或base64编码字符串

    Returns:
        MIME类型字符串，如 'image/jpeg', 'image/png' 等

    Raises:
        ValueError: 如果无法识别图片格式
    """
    # 如果输入是字符串，先转换为字节
    byte_data: bytes = base64_to_bytes(data) if isinstance(data, str) else bytes(data)

    # 使用 filetype 库检测图片类型
    kind = filetype.guess(byte_data)

    if kind is None:
        raise ValueError("无法识别的图片格式")

    # 返回 MIME 类型
    return kind.mime


async def download_image(url: str, client: httpx.AsyncClient) -> bytes:
    """
    异步下载图片

    Args:
        url: 图片的URL地址
        client: httpx异步客户端实例

    Returns:
        图片的字节数据

    Raises:
        httpx.HTTPError: 当HTTP请求失败时
    """
    response = await client.get(url)
    response.raise_for_status()
    return response.content
