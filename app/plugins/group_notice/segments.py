from pydantic_settings import BaseSettings
from pydantic import BaseModel
from typing import Any


class GroupConfig(BaseModel):
    group_id: int


class PluginConfig(BaseSettings):
    group_config: list[GroupConfig]
    firecrawl_config: Any | None = None
