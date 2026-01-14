from pydantic_settings import BaseSettings
from pydantic import BaseModel


class GroupConfig(BaseModel):
    group_id: int


class PluginConfig(BaseSettings):
    group_config: list[GroupConfig]
