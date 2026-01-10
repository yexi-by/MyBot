"""API Payload 模型包

按功能分类的 Payload 模型，方便导入使用。

使用方式:
    from app.models.api.payloads import base, group, file, album, account, system, stream

    # 或者直接导入具体的 Payload
    from app.models.api.payloads.base import SendPokePayload
"""

from . import account, album, base, file, group, system, stream

__all__ = [
    "base",
    "group",
    "file",
    "album",
    "account",
    "system",
    "stream",
]
