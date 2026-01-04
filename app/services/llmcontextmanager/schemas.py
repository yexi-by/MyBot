from typing import Literal
from pydantic import BaseModel, model_validator


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    text: str | None = None
    image: bytes | None = None

    @model_validator(mode="after")
    def check_at_least_one(self):
        if self.text is None and self.image is None:
            raise ValueError("必须提供 text 或 image")
        return self

class LLMContextConfig(BaseModel):
    system_prompt_path: str
    max_context_length: int
