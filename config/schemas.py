from pydantic_settings import BaseSettings
from app.services import LLMConfig, EmbeddingConfig
from app.database.schemas import RedisConfig


class Settings(BaseSettings):
    llm_settings: list[LLMConfig] = []
    embedding_settings: EmbeddingConfig
    faiss_file_location: str = ""
    redis_config: RedisConfig 
    video_and_image_path: str
    
    
