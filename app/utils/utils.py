import aiofiles
from typing import Any
from pathlib import Path
import json
import base64


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
