import inspect
from typing import Any, Callable, get_origin, Union, get_args
from types import UnionType
from collections import defaultdict
from app.models import AllEvent
from app.api import BOTClient
from app.plugins import PLUGINS, BasePlugin, PluginContext
from app.services import LLMHandler, SearchVectors, SiliconFlowEmbedding


class PluginController:
    def __init__(
        self,
        llm: LLMHandler,
        siliconflow: SiliconFlowEmbedding,
        search_vectors: SearchVectors,
        bot: BOTClient,
    ) -> None:
        self.llm = llm
        self.siliconflow = siliconflow
        self.search_vectors = search_vectors
        self.bot = bot
        self.plugin_objects: list[BasePlugin] = []
        self.handlers_map: dict[type[AllEvent], list[tuple[Callable, str]]] = (
            defaultdict(list)
        )
        self._load_plugins()

    @staticmethod
    def get_dependency(func: Callable[..., Any]) -> dict[str, type[AllEvent]]:
        sig = inspect.signature(func)
        valid_params = [p for p in sig.parameters.values()]
        if len(valid_params) != 1:
            raise ValueError(
                f"插件定义错误: 方法 '{func.__name__}' 必须且只能接受 1 个事件参数。"
            )
        dependencies = {}
        param = valid_params[0]
        if param.annotation is inspect.Parameter.empty:
            raise ValueError(f"错误: 参数 '{param.name}' 缺少类型注解")
        dependencies[param.name] = param.annotation
        return dependencies

    def _load_plugins(self) -> None:
        for cls in PLUGINS:
            dependencies = self.get_dependency(cls.run)
            param_name, event_type = next(iter(dependencies.items()))

            plugin_object = cls(
                context=PluginContext(
                    llm=self.llm,
                    siliconflow=self.siliconflow,
                    search_vectors=self.search_vectors,
                    bot=self.bot,
                )
            )
            self.plugin_objects.append(plugin_object)
            origin = get_origin(event_type)
            if origin is Union or origin is UnionType:
                args = get_args(event_type)
                for arg in args:
                    self.handlers_map[arg].append(
                        (plugin_object.add_to_queue, param_name)
                    )
            else:
                self.handlers_map[event_type].append(
                    (plugin_object.add_to_queue, param_name)
                )
