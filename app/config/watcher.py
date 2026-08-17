"""自动监听统一配置目录并发布插件配置变化。"""

import asyncio
from pathlib import Path

from watchfiles import awatch  # pyright: ignore[reportUnknownVariableType]

from app.utils.log import log_event, log_exception

from .manager import ConfigManager

_WATCH_DEBOUNCE_MS = 500
_WATCH_STEP_MS = 50


class ConfigWatcher:
    """只响应主配置和当前插件实际引用的文件。"""

    def __init__(self, *, manager: ConfigManager) -> None:
        """绑定配置管理器和停止事件。"""
        self.manager = manager
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """持续监听配置目录，单次失败保留旧配置并继续监听。"""
        try:
            async for changes in awatch(
                self.manager.config_root,
                debounce=_WATCH_DEBOUNCE_MS,
                step=_WATCH_STEP_MS,
                stop_event=self._stop_event,
            ):
                changed_paths = {
                    Path(raw_path).resolve(strict=False) for _, raw_path in changes
                }
                interesting_paths = self.manager.watched_files
                if changed_paths.isdisjoint(interesting_paths):
                    continue
                result = await asyncio.to_thread(self.manager.reload)
                if result.error is not None:
                    for issue in result.error.issues:
                        log_event(
                            level="ERROR",
                            event="config.reload.failed",
                            category="config",
                            message="插件配置热加载失败，继续使用旧配置",
                            location=issue.location,
                            error_type=issue.error_type,
                            error=issue.message,
                            active_revision=result.revision,
                        )
                    continue
                if result.restart_required_sections:
                    log_event(
                        level="WARNING",
                        event="config.reload.restart_required",
                        category="config",
                        message="启动配置已经变化，本次只应用插件配置",
                        sections=list(result.restart_required_sections),
                    )
                if not result.applied:
                    continue
                log_event(
                    level="SUCCESS",
                    event="config.reload.applied",
                    category="config",
                    message="插件配置热加载完成",
                    revision=result.revision,
                    plugins=list(result.changed_plugins),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_exception(
                event="config.watcher.failed",
                category="config",
                message="配置目录监听异常退出",
                exc=exc,
            )

    def stop(self) -> None:
        """通知 watcher 结束。"""
        self._stop_event.set()
