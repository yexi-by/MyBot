"""文件读写工具。"""

from pathlib import Path

import aiofiles


async def read_text_file_async(file_path: str | Path) -> str:
    """异步读取 UTF-8 文本文件。"""
    path = Path(file_path)
    async with aiofiles.open(path, mode="r", encoding="utf-8") as file:
        content = await file.read()
    return content
