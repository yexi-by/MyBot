from pydantic import BaseModel, model_validator, ConfigDict
from typing import Literal
from .base import LLMProvider

class LLMConfig(BaseModel):
    api_key: str
    base_url: str
    model_vendors: str  # 模型厂商
    provider_type: str
    retry_count: int
    retry_delay: int


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    text: str | None = None
    image: bytes | None = None

    @model_validator(mode="after")
    def check_at_least_one(self):
        if self.text is None and self.image is None:
            raise ValueError("必须提供 text 或 image")
        return self


class LLMProviderWrapper(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )  # 允许在 Pydantic使用自定义类类型
    model_vendors: str
    provider: LLMProvider
