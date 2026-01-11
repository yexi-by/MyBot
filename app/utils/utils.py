import base64
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal, overload

import aiofiles
import filetype
import httpx
from pydantic import BaseModel

# 调试文件名
DEBUG_FILENAME = "debug/debug.jsonl"


async def save_debug_jsonl(
    data: dict[str, Any], directory_path: str | Path | None = None
) -> None:
    """
    将字典数据追加写入到调试用的 JSONL 文件中。

    Args:
        data: 要写入的字典数据。
        directory_path: 目录路径，默认为当前工作目录。
    """
    if not directory_path:
        directory_path = Path.cwd()
    path = Path(directory_path) / DEBUG_FILENAME
    async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False) + "\n")


def read_toml_file(file_path: str | Path) -> dict:
    """
    读取 TOML 文件并返回字典。

    Args:
        file_path: 文件路径。

    Returns:
        dict: 解析后的 TOML 数据。
    """
    path = Path(file_path)
    with open(path, "rb") as f:
        toml_data = tomllib.load(f)
        return toml_data


def base64_to_bytes(data: str) -> bytes:
    """
    将 Base64 字符串转换为字节。

    Args:
        data: Base64 编码的字符串，可以包含前缀（如 data:image/png;base64,）。

    Returns:
        bytes: 解码后的字节数据。
    """
    if "," in data:
        _, data = data.split(",", 1)
    return base64.b64decode(data)


async def read_text_file_async(file_path: str | Path) -> str:
    """
    异步读取文本文件内容。

    Args:
        file_path: 文件路径。

    Returns:
        str: 文件内容。
    """
    path = Path(file_path)
    async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
        content = await f.read()
    return content


def read_text_file_sync(file_path: str | Path) -> str:
    """
    同步读取文本文件内容。

    Args:
        file_path: 文件路径。

    Returns:
        str: 文件内容。
    """
    path = Path(file_path)
    with open(path, mode="r", encoding="utf-8") as f:
        content = f.read()
    return content


def detect_mime_type(data: bytes | str) -> str:
    """
    检测文件格式并返回 MIME 类型。

    使用 filetype 库通过文件头魔数判断文件类型。

    Args:
        data: 文件的字节数据或 Base64 编码字符串。

    Returns:
        str: MIME 类型字符串，如 'image/jpeg', 'application/pdf' 等。

    Raises:
        ValueError: 如果无法识别文件格式。
    """
    byte_data: bytes = base64_to_bytes(data) if isinstance(data, str) else bytes(data)
    kind = filetype.guess(byte_data)
    if kind is None:
        raise ValueError("无法识别的文件格式")
    return kind.mime


def detect_extension(data: bytes) -> str:
    """
    使用 filetype 库检测文件后缀名。

    Args:
        data: 文件字节数据。

    Returns:
        str: 带点的后缀名（如 '.png'），识别失败返回 'unknown'。
    """
    kind = filetype.guess(data)
    if kind is None:
        return "unknown"
    return "." + kind.extension


async def download_content(url: str, client: httpx.AsyncClient) -> bytes:
    """
    异步下载内容。

    Args:
        url: 内容的 URL 地址。
        client: httpx 异步客户端实例。

    Returns:
        bytes: 下载的字节数据。

    Raises:
        httpx.HTTPError: 当 HTTP 请求失败时。
    """
    response = await client.get(url)
    response.raise_for_status()
    return response.content


@overload
def read_file_content(
    file_path: str | Path, output_type: Literal["bytes"]
) -> bytes: ...


@overload
def read_file_content(
    file_path: str | Path, output_type: Literal["base64"]
) -> str: ...


def read_file_content(
    file_path: str | Path, output_type: Literal["bytes", "base64"]
) -> bytes | str:
    """
    读取文件并返回二进制数据或 Base64 字符串。

    Args:
        file_path: 文件路径。
        output_type: 'bytes' 返回原始二进制, 'base64' 返回纯 Base64 编码字符串。

    Returns:
        bytes | str: 文件数据。
    """
    path_obj = Path(file_path)
    file_bytes = path_obj.read_bytes()
    if output_type == "base64":
        return base64.b64encode(file_bytes).decode("utf-8")
    return file_bytes


def load_config[T](file_path: str | Path, model_cls: type[T]) -> T:
    """
    从 TOML 文件加载配置并返回对应对象。

    Args:
        file_path: TOML 配置文件路径。
        model_cls: Pydantic 模型类。

    Returns:
        T: 配置对象实例。
    """
    path = Path(file_path)
    config_data = read_toml_file(file_path=path)
    config = model_cls(**config_data)
    return config


def pydantic_to_json_schema(model_class: type[BaseModel]) -> str:
    """
    将 Pydantic 模型转换为 LLM 能理解的 JSON Schema 字符串。

    Args:
        model_class: Pydantic 模型类。

    Returns:
        str: JSON Schema 字符串。
    """
    schema = json.dumps(model_class.model_json_schema(), indent=2, ensure_ascii=False)
    return schema


def clean_ai_json_response(text: str) -> str:
    """
    智能清理 AI 返回的 JSON 字符串。

    优先寻找最外层的 {} 结构，
    只有当找不到外层 {} 且存在 ```json 包裹时才处理 Markdown。

    Args:
        text: AI 返回的原始文本。

    Returns:
        str: 清理后的 JSON 字符串。
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


def parse_validated_json[T: BaseModel](raw_response: str, model_cls: type[T]) -> T:
    """
    清洗并验证 AI 返回的 JSON 字符串。

    Args:
        raw_response: AI 返回的原始文本。
        model_cls: Pydantic 模型类。

    Returns:
        T: 验证后的模型实例。
    """
    cleaned_json = clean_ai_json_response(raw_response)
    return model_cls.model_validate_json(cleaned_json)
