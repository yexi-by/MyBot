from abc import ABCMeta, abstractmethod
from typing import cast
from dataclasses import dataclass
from app.services import LLMHandler, SiliconFlowEmbedding, SearchVectors
from app.api import BOTClient

PLUGINS: list[type["BasePlugin"]] = []


@dataclass
class PluginContext:
    llm: LLMHandler
    siliconflow: SiliconFlowEmbedding
    search_vectors: SearchVectors
    bot: BOTClient


class PluginMeta(ABCMeta):
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        if bases:
            PLUGINS.append(cast(type["BasePlugin"], cls))
        return cls


class BasePlugin(metaclass=PluginMeta):
    def __init__(self, context:PluginContext):
        self.context = context
        self.setup()

    @abstractmethod
    def setup(self):
        pass

    @abstractmethod
    async def run(self)->bool:
        pass
