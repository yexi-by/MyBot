from app.services import ContextHandler
from .segments import PluginConfig
from app.utils import (
    read_text_file_sync,
)

MAX_CONTEXT_LENGTH = 5

"""
ai生图功能应该轮次应该是这样的 (系统提示词+用户提示词)->得到nai能理解的提示词->发送给nai生图 一轮次结束 不做上下文管理
"""


def build_group_chat_contexts(
    config: PluginConfig, schema: str
) -> dict[int, ContextHandler]:
    """根据配置构建系统提示词"""
    group_contexts: dict[int, ContextHandler] = {}
    for group_settings in config.group_config:
        system_prompt = read_text_file_sync(file_path=group_settings.system_prompt_path)
        combined_prompt = "\n\n".join([system_prompt, schema])
        group_contexts[group_settings.group_id] = ContextHandler(
            system_prompt=combined_prompt, max_context_length=MAX_CONTEXT_LENGTH
        )
    return group_contexts
