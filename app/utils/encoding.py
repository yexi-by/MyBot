"""编码转换工具。"""

import base64


def base64_to_bytes(data: str) -> bytes:
    """将 Base64 字符串转换为字节数据。"""
    if "," in data:
        _, data = data.split(",", 1)
    return base64.b64decode(data)
