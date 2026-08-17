"""WebUI 配置控制台后端公共导出。"""

from .routes import create_webui_router
from .spa import mount_webui_static

__all__ = [
    "create_webui_router",
    "mount_webui_static",
]
