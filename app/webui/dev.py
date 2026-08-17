"""WebUI 独立开发入口：不依赖 PostgreSQL，只提供配置 API 与前端产物。"""

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from app.config import CONFIG_FILE, ConfigManager

from .power import PowerController
from .routes import create_webui_router
from .spa import mount_webui_static


def create_dev_app(
    *, config_file: Path, power: PowerController | None = None
) -> FastAPI:
    """创建只挂载 WebUI 能力的最小 FastAPI 应用。"""
    manager = ConfigManager.create(config_file=config_file)
    app = FastAPI(title="MyBot WebUI Dev")
    app.include_router(
        create_webui_router(manager=manager, watcher_active=False, power=power)
    )
    mount_webui_static(app)
    return app


def main() -> None:
    """启动 WebUI 开发服务器。"""
    parser = argparse.ArgumentParser(description="MyBot WebUI 开发服务器")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE, help="mybot.toml 路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6056)
    namespace = parser.parse_args()
    power = PowerController()
    app = create_dev_app(config_file=Path(str(namespace.config)), power=power)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=str(namespace.host),
            port=int(namespace.port),
            log_level="info",
        )
    )
    power.bind(server)
    server.run()


if __name__ == "__main__":
    main()
