"""WebUI 电源控制：通过 uvicorn Server 句柄延迟触发优雅停机。"""

import asyncio
from typing import Literal

import uvicorn

from app.utils.log import log_event

PowerAction = Literal["restart", "shutdown"]


class PowerController:
    """持有 uvicorn Server 句柄，延迟触发优雅停机，让 HTTP 响应先返回。

    restart 与 shutdown 在进程级行为一致，都是优雅退出；进程是否重新拉起
    完全由外部守护策略决定（Docker `restart: unless-stopped` 会把两者都拉起，
    本机裸跑时两者都保持停止，需要手动启动）。
    """

    def __init__(self, *, delay_seconds: float = 0.5) -> None:
        self._server: uvicorn.Server | None = None
        self._delay_seconds = delay_seconds

    def bind(self, server: uvicorn.Server) -> None:
        """绑定由进程入口创建的 uvicorn Server。"""
        self._server = server

    @property
    def available(self) -> bool:
        """是否已绑定可操作的 Server 句柄。"""
        return self._server is not None

    def request(self, action: PowerAction) -> None:
        """在短暂延迟后触发优雅停机；未绑定句柄时抛出 RuntimeError。"""
        server = self._server
        if server is None:
            raise RuntimeError("电源控制未绑定服务句柄")
        loop = asyncio.get_running_loop()
        loop.call_later(self._delay_seconds, self._trigger, server, action)

    @staticmethod
    def _trigger(server: uvicorn.Server, action: PowerAction) -> None:
        log_event(
            level="WARNING",
            event=f"webui.power.{action}",
            category="webui",
            message=(
                "WebUI 请求重启进程，正在优雅停机"
                if action == "restart"
                else "WebUI 请求关机，正在优雅停机"
            ),
        )
        server.should_exit = True
