"""API 包

提供 QQ Bot 客户端接口。
"""

from . import mixins
from .client import BOTClient

__all__ = ["BOTClient", "mixins"]
