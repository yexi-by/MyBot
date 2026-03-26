"""ai_repose 插件的数据模型定义。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings

from .firecrawl_model import Firecrawl
from .message_model import GroupFile, MessageContent


class AIResponse(BaseModel):
    """定义大模型返回给 ai_repose 插件的结构化响应。"""

    model_config = ConfigDict(extra="forbid")

    send_message: Annotated[
        MessageContent | None,
        Field(description="如果判断需要向群聊发送消息,则填充此字段;无话可说，则留空"),
    ] = None
    group_file: Annotated[
        GroupFile | None,
        Field(
            description="如果用户请求查看群文件列表（根目录或子文件夹）或获取特定文件的信息/下载链接，请填充此字段。"
        ),
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
    """定义单个群聊的上下文配置。"""

    group_id: int
    system_prompt_path: str
    knowledge_base_path: str
    function_path: str
    max_context_length: int


class FirecrawlConfig(BaseModel):
    """定义 Firecrawl 客户端配置。"""

    api_key: str
    api_url: str


class PluginConfig(BaseSettings):
    """定义 ai_repose 插件的顶层配置。"""

    group_config: list[GroupConfig]
    firecrawl_config: FirecrawlConfig
    model_name: str
    model_vendors: str
