from pydantic import BaseModel, model_validator, field_serializer
from typing import Literal
from .base import LLMProvider
from dataclasses import dataclass


class LLMConfig(BaseModel):
    api_key: str
    base_url: str
    model_vendors: str  # 模型厂商
    provider_type: str  # 接口类型
    retry_count: int
    retry_delay: int


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    text: str | None = None
    image: list[bytes] | None = None

    @model_validator(mode="after")
    def check_at_least_one(self):
        if self.text is None and self.image is None:
            raise ValueError("必须提供 text 或 image")
        return self

    @field_serializer("image")
    def serialize_image(self, image: list[bytes] | None, _info):
        if image is None:
            return None
        image_str_lst: list[str] = [
            f"此图片字节码长度为{len(image_bytes)}" for image_bytes in image
        ]
        return image_str_lst


@dataclass
class LLMProviderWrapper:
    model_vendors: str
    provider: LLMProvider
