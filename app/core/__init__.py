"""核心服务模块公共导出。"""

from .server import NapCatServer
from .di import MyProvider

__all__ = ["NapCatServer", "MyProvider"]
