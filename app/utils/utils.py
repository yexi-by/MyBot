import asyncio
import base64
import io
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal, overload

import aiofiles
import filetype
import httpx
import pandas as pd
from pydantic import BaseModel
from pypdf import PdfReader

# 调试文件名
DEBUG_FILENAME = "debug/debug.jsonl"


async def write_to_file(
    data: dict[str, Any], directory_path: str | Path | None = None
) -> None:
    if not directory_path:
        directory_path = Path.cwd()
    path = Path(directory_path) / DEBUG_FILENAME
    async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False) + "\n")


def load_toml_file(file_path: str | Path) -> dict:
    path = Path(file_path)
    with open(path, "rb") as f:
        toml_data = tomllib.load(f)
        return toml_data


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
    检测文件格式并返回MIME类型

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


# def detect_extension(data: bytes) -> str:
#     """
#     使用 filetype 库检测后缀名。
#     返回不带点的后缀名（如 'png'），识别失败返回 'unknown'。
#     """
#     kind = filetype.guess(data)
#     if kind is None:
#         return "unknown"
#     return "." + kind.extension


async def download_image(url: str, client: httpx.AsyncClient) -> bytes:
    """
    异步下载

    Args:
        url: 文件的URL地址
        client: httpx异步客户端实例

    Returns:
        字节数据

    Raises:
        httpx.HTTPError: 当HTTP请求失败时
    """
    response = await client.get(url)
    response.raise_for_status()
    return response.content


@overload
def image_to_bytes_pathlib(
    image_path: str | Path, output_type: Literal["bytes"]
) -> bytes: ...


@overload
def image_to_bytes_pathlib(
    image_path: str | Path, output_type: Literal["base64"]
) -> str: ...


def image_to_bytes_pathlib(
    image_path: str | Path, output_type: Literal["bytes", "base64"]
) -> bytes | str:
    """
    读取图片并返回二进制数据或Base64字符串。

    Args:
        image_path: 图片路径
        output_type: 'bytes' 返回原始二进制, 'base64' 返回纯Base64编码字符串
    """
    path_obj = Path(image_path)
    file_bytes = path_obj.read_bytes()
    if output_type == "base64":
        return base64.b64encode(file_bytes).decode("utf-8")
    return file_bytes


def load_config[T](file_path: str | Path, model_cls: type[T]) -> T:
    """从TOML文件加载配置并返回对应对象"""
    path = Path(file_path)
    config_data = load_toml_file(file_path=path)
    config = model_cls(**config_data)
    return config


def convert_basemodel_to_schema(model_class: type[BaseModel]) -> str:
    """将BaseModel模型转换为LLM能理解的JSON Schema字符串"""
    schema = json.dumps(model_class.model_json_schema(), indent=2, ensure_ascii=False)
    return schema


def clean_ai_json_response(text: str) -> str:
    """
    智能清理 AI 返回的 JSON 字符串。
    优先寻找最外层的 {} 结构，
    只有当找不到外层 {} 且存在 ```json 包裹时才处理 Markdown。
    """
    text = text.strip()
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        potential_json = text[first_brace : last_brace + 1]
        return potential_json
    pattern = r"^```(?:json)?\s*(.*?)```$"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def parse_pdf(file_bytes: bytes) -> str:
    """解析PDF文件内容"""
    with io.BytesIO(file_bytes) as stream:
        reader = PdfReader(stream)
        text_list = [
            page.extract_text() for page in reader.pages if page.extract_text()
        ]
    text = "".join(text_list)
    return text


def parse_excel(file_bytes: bytes) -> str:
    """解析excel内容"""
    with io.BytesIO(file_bytes) as stream:
        df = pd.read_excel(stream)
        text = df.to_markdown(index=False)
    return text


async def bytes_to_text(
    file_bytes: bytes, file_extension: Literal[".txt", ".pdf", ".xlsx", ".xls"]
):
    match file_extension:
        case ".pdf":
            text = await asyncio.to_thread(parse_pdf, file_bytes)
        case ".xlsx" | ".xls":
            text = await asyncio.to_thread(parse_excel, file_bytes)
        case ".txt":
            text = file_bytes.decode("utf-8")
        case _:
            raise ValueError(f"不支持的文件扩展名: {file_extension}")
    return text
    return text
