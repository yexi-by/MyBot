from pydantic_settings import BaseSettings
from app.services import LLMConfig, EmbeddingConfig,LLMContextConfig
from app.database.schemas import RedisConfig




class Settings(BaseSettings):
    llm_settings: list[LLMConfig] = []
    llm_context_config:LLMContextConfig
    embedding_settings: EmbeddingConfig
    faiss_file_location: str = ""
    redis_config: RedisConfig 
    video_and_image_path: str
    
    
