import aiofiles
from typing import Any
from pathlib import Path
import json


async def write_to_file(
    data: dict[str, Any], directory_path: str | Path | None = None
) -> None:
    if not directory_path:
        directory_path = Path.cwd()
    path = Path(directory_path) / "debug.jsonl"
    async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False) + "\n")
