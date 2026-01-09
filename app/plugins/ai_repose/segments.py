from pydantic import BaseModel, Field
from typing import Annotated
from pydantic_settings import BaseSettings
from .message_model import MessageContent
from .firecrawl_model import Firecrawl


class AIResponse(BaseModel):
    """AI响应消息模型，包含可选的发送消息内容"""

    send_message: Annotated[
        MessageContent | None,
        Field(description="如果判断需要向群聊发送消息,则填充此字段;无话可说，则留空"),
    ] = None
    firecrawl: Annotated[
        Firecrawl | None,
        Field(
            description="如果需要使用 Firecrawl 进行网页抓取、搜索或分析，请填充此字段"
        ),
    ] = None
    end: Annotated[
        bool,
        Field(
            description="结束对话标志。当任务已完成，没有其他任务需要处理，且本轮对话应当结束时，请将其设定为 True。这将指示系统退出处理循环。"
        ),
    ] = False


class GroupConfig(BaseModel):
    """群组配置模型，定义单个群组的所有配置信息"""

    group_id: int
    system_prompt_path: str
    knowledge_base_path: str
    function_path: str
    max_context_length: int


class FirecrawlConfig(BaseModel):
    api_key: str
    api_url: str


class PluginConfig(BaseSettings):
    """全局配置模型，包含所有群组的配置列表"""

    group_config: list[GroupConfig]
    firecrawl_config: FirecrawlConfig
