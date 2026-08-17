"""应用启动入口。"""

import uvicorn

from app.config import ConfigLoadError, ConfigManager
from app.utils.log import configure_logging


def main() -> None:
    """启动 NapCat 反向 WebSocket 服务。"""
    try:
        config_manager = ConfigManager.create()
    except ConfigLoadError as exc:
        raise SystemExit(f"配置加载失败: {exc}") from exc
    config = config_manager.boot_config
    configure_logging(
        log_dir=config.logging.directory,
        console_level=config.logging.console_level,
        file_level=config.logging.file_level,
        retention=config.logging.retention,
        rotation=config.logging.rotation,
        compression=config.logging.compression,
    )

    from dishka import make_async_container

    from app.core import NapCatServer, MyProvider

    container = make_async_container(MyProvider(config_manager=config_manager))
    napcat = NapCatServer(
        container=container, config=config, config_manager=config_manager
    )
    uvicorn.run(
        napcat.app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level,
        access_log=config.server.access_log,
    )


if __name__ == "__main__":
    main()
