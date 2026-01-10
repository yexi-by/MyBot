from pydantic_settings import BaseSettings
from app.services import LLMConfig, EmbeddingConfig, LLMContextConfig
from app.database.schemas import RedisConfig
from app.services.ai_image import NaiImageConfig


class Settings(BaseSettings):
    llm_settings: list[LLMConfig] = []
    llm_context_config: LLMContextConfig | None = None
    embedding_settings: EmbeddingConfig | None = None
    faiss_file_location: str = ""
    redis_config: RedisConfig
    video_and_image_path: str
    proxy: str | None = None
    nai_settings: NaiImageConfig | None = None
    password:str
