"""应用启动入口。"""

import uvicorn

from app.config import load_settings
from app.utils.log import configure_logging


def main() -> None:
    """启动 NapCat 反向 WebSocket 服务。"""
    settings = load_settings()
    configure_logging(
        log_dir=settings.logging.directory,
        console_level=settings.logging.console_level,
        file_level=settings.logging.file_level,
        retention=settings.logging.retention,
        rotation=settings.logging.rotation,
        compression=settings.logging.compression,
    )

    from dishka import make_async_container

    from app.core import NapCatServer, MyProvider

    container = make_async_container(MyProvider(settings=settings))
    napcat = NapCatServer(container=container, settings=settings)
    uvicorn.run(
        napcat.app,
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level,
        access_log=settings.server.access_log,
    )


if __name__ == "__main__":
    main()
