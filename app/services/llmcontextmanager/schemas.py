from pydantic import BaseModel


class LLMContextConfig(BaseModel):
    system_prompt_path: str
    max_context_length: int
