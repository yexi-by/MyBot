from pydantic import BaseModel

class VectorStorage(BaseModel):
    name:str
    vectors:list[float]
    
class VectorizeConfig(BaseModel):
    tokens_per_minute:int=60
    consumer_count: int=60
    min_chunk_size:int=1000
    max_chunk_size:int=1200
    max_line:int=10
    
class EmbeddingConfig(BaseModel):
    api_key: str
    base_url: str
    model_name: str
    provider_type: str
    retry_count: int
    retry_delay: int