import inspect
from typing import Any, Callable, Coroutine
from .event_parser import EventTypeChecker
from .plugin_manager import PluginController


class EventDispatcher:
    """中央逻辑处理器"""

    def __init__(
        self,
        checker: EventTypeChecker,
        plugincontroller: PluginController,
    ) -> None:
        self.checker = checker
        self.plugincontroller = plugincontroller
        self.plugins: list[tuple[Callable[..., Coroutine[Any, Any, bool]], Any]] = []
        self._initialize_plugin_list()

    @staticmethod
    def get_dependency(func: Callable[..., Any]) -> Any:
        sig = inspect.signature(func)
        if len(sig.parameters) != 1:
            raise ValueError("错误: 依赖数量必须且只能为 1")
        param = next(iter(sig.parameters.values()))
        if param.annotation is inspect.Parameter.empty:
            raise ValueError(f"错误: 参数 '{param.name}' 缺少类型注解")
        return param.annotation

    def _initialize_plugin_list(self) -> None:
        """初始化插件"""
        for plugin in self.plugincontroller.plugin_objects:
            annotation = self.get_dependency(plugin.run)
            self.plugins.append((plugin.run, annotation))

    async def dispatch_event(self, data: dict[str, Any]) -> None:
        event = self.checker.get_event(data)
        for plugin_run, annotation in self.plugins:
            if not isinstance(event, annotation):
                continue
            return_value = await plugin_run(event)
            if return_value:
                break
