import inspect
from typing import Any, Callable, Coroutine

from models import AllEvent

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
        self.dependencies_list: list[
            tuple[Callable[..., Coroutine[Any, Any, bool]], dict[str, type[AllEvent]]]
        ] = []
        self._load_plugins()

    @staticmethod
    def get_dependency(func: Callable[..., Any]) -> dict[str, type[AllEvent]]:
        sig = inspect.signature(func)
        dependencies = {}
        for param in sig.parameters.values():
            if param.name in ("self", "cls"):
                continue
            if param.annotation is inspect.Parameter.empty:
                raise ValueError(f"错误: 参数 '{param.name}' 缺少类型注解")
            dependencies[param.name] = param.annotation
        return dependencies

    def _load_plugins(self) -> None:
        for cls in PLUGINS:
            dependencies = self.get_dependency(cls.run)
            plugin_object = cls(
                context=PluginContext(
                    llm=self.llm,
                    siliconflow=self.siliconflow,
                    search_vectors=self.search_vectors,
                    bot=self.bot,
                )
            )
            self.plugin_objects.append(plugin_object)
            self.dependencies_list.append((plugin_object.add_to_queue, dependencies))
