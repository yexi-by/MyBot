"""AI 群聊插件配置模型和提示词加载。"""

from pathlib import Path

from pydantic import Field

from app.config.plugin_config import load_plugin_config
from app.models import NapCatId, StrictModel

from .constants import CONFIG_PATH, CONFIG_SECTION


class GroupChatConfig(StrictModel):
    """单个群聊的 AI 回复配置。"""

    group_id: NapCatId
    system_prompt_path: str
    knowledge_base_path: str
    max_context_tokens: int = Field(gt=0)


class AIGroupChatConfig(StrictModel):
    """AI 智能群聊插件配置。"""

    model_name: str
    model_vendors: str
    supports_multimodal: bool = False
    max_tool_rounds: int = Field(default=8, ge=1)
    token_estimation_safety_factor: float = Field(default=1.25, ge=1)
    context_compression_notice: str = "上下文有点长，我先整理一下记忆，稍等我几秒喵~"
    output_reasoning_content: bool = False
    pass_back_reasoning_content: bool = False
    debug_dump_messages: bool = False
    enable_deepseek_v4_roleplay_instruct: bool = False
    deepseek_v4_extra_requirements_path: str = (
        "plugins_config/ai_group_chat/prompts/deepseek_v4/extra_requirements.md"
    )
    deepseek_v4_roleplay_instruct_path: str = (
        "plugins_config/ai_group_chat/prompts/deepseek_v4/roleplay_instruct.md"
    )
    allow_mention_all: bool = False
    persist_tool_results: bool = False
    group_config: list[GroupChatConfig]


def load_ai_group_chat_config() -> AIGroupChatConfig:
    """读取并校验 AI 群聊插件配置。"""
    return load_plugin_config(
        section_name=CONFIG_SECTION,
        model_cls=AIGroupChatConfig,
        config_path=CONFIG_PATH,
    )


def build_system_prompt(*, group_config: GroupChatConfig) -> str:
    """组合角色提示词和知识库。"""
    system_prompt = _read_text_file(group_config.system_prompt_path)
    knowledge_base = _read_text_file(group_config.knowledge_base_path)
    return "\n\n".join([system_prompt, knowledge_base])


def _read_text_file(file_path: str) -> str:
    """读取 UTF-8 文本配置文件。"""
    path = Path(file_path)
    return path.read_text(encoding="utf-8")
