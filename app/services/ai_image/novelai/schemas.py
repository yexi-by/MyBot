from pydantic import BaseModel

class NaiImageConfig(BaseModel):
    api_key: str
    base_url: str