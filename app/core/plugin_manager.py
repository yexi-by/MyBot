from app.plugins import PLUGINS
from app.services import LLMHandler, SiliconFlowEmbedding, SearchVectors
from app.api import BOTClient
from app.plugins.base import PluginContext,BasePlugin


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
        self.plugin_objects:list[BasePlugin] = []
        self._load_plugins()

    def _load_plugins(self) -> None:
        for cls in PLUGINS:
            plugin_object = cls(
                context=PluginContext(
                    llm=self.llm,
                    siliconflow=self.siliconflow,
                    search_vectors=self.search_vectors,
                    bot=self.bot,
                )
            )
            self.plugin_objects.append(plugin_object)
