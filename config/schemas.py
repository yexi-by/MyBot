from pydantic_settings import BaseSettings
from app.services import LLMConfig, EmbeddingConfig



class Settings(BaseSettings):
    llm_settings: list[LLMConfig] = []
    embedding_settings: EmbeddingConfig
    faiss_file_location: str = ""
    
