from pydantic import BaseModel
from app.services.ai_image.novelai.payload import CharCaption
from pydantic_settings import BaseSettings

class NaiImageKwargs(BaseModel):
    prompt:str
    negative_prompt:str
    v4_prompt_char_captions:list[CharCaption]
    
#---------------------------------------#
class GroupConfig(BaseModel):
    group_id: int
    system_prompt_path: str

class PluginConfig(BaseSettings):
    """全局配置模型，包含所有群组的配置列表"""

    group_config: list[GroupConfig]
    

    
    