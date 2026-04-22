from pydantic_settings import BaseSettings

from app.database.schemas import RedisConfig
from app.services import EmbeddingConfig, LLMConfig, LLMContextConfig


class Settings(BaseSettings):
    llm_settings: list[LLMConfig] = []
    llm_context_config: LLMContextConfig | None = None
    embedding_settings: EmbeddingConfig | None = None
    faiss_file_location: str = ""
    redis_config: RedisConfig
    video_and_image_path: str
    proxy: str | None = None
    password: str
