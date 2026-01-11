import asyncio
import io
from typing import Literal

import pandas as pd
from pypdf import PdfReader


def parse_pdf(file_bytes: bytes) -> str:
    """
    解析PDF文件内容。

    Args:
        file_bytes: PDF文件的字节数据。

    Returns:
        str: 提取的文本内容。
    """
    with io.BytesIO(file_bytes) as stream:
        reader = PdfReader(stream)
        text_list = [
            page.extract_text() for page in reader.pages if page.extract_text()
        ]
    text = "".join(text_list)
    return text


def parse_excel(file_bytes: bytes) -> str:
    """
    解析Excel文件内容并转换为Markdown格式。

    Args:
        file_bytes: Excel文件的字节数据。

    Returns:
        str: 转换后的Markdown表格字符串。
    """
    with io.BytesIO(file_bytes) as stream:
        df = pd.read_excel(stream)
        text = df.to_markdown(index=False)
    return text


async def bytes_to_text(
    file_bytes: bytes, file_extension: Literal[".txt", ".pdf", ".xlsx", ".xls"] | str
) -> str:
    """
    将不同格式的文件字节数据转换为文本。

    支持 .txt, .pdf, .xlsx, .xls 格式。

    Args:
        file_bytes: 文件字节数据。
        file_extension: 文件扩展名（包含点，如 .txt）。

    Returns:
        str: 提取的文本内容。

    Raises:
        ValueError: 如果不支持该文件扩展名。
    """
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
