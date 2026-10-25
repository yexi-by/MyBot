"""应用入口；显式指定配置，不复制或迁移独立远端配置。"""

import argparse
from pathlib import Path

import uvicorn

from .diagnostics import configure_logging
from .web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ Agent Harness")
    parser.add_argument("--config", type=Path, default=Path("config/harness.toml"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    options = parser.parse_args()
    configure_logging()
    while True:
        app = create_app(options.config)
        settings = app.state.store.settings.server
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=options.host or settings.host,
                port=options.port or settings.port,
                access_log=False,
                ws_max_size=app.state.store.settings.napcat.media_max_bytes * 4 // 3 + 1_048_576,
            )
        )
        restart = False

        def shutdown(should_restart: bool) -> None:
            nonlocal restart
            restart = should_restart
            server.should_exit = True

        app.state.shutdown = shutdown
        server.run()
        if not restart:
            break


if __name__ == "__main__":
    main()
