"""AI 群聊插件配置模型和提示词加载。"""

from pathlib import Path

from pydantic import Field, model_validator

from app.config.plugin_config import load_plugin_config
from app.models import NapCatId, StrictModel

from .constants import CONFIG_PATH, CONFIG_SECTION, DEEPSEEK_V4_ROLEPLAY_MODELS


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
    multimodal_fallback_model_name: str | None = None
    multimodal_fallback_model_vendors: str | None = None
    max_tool_rounds: int = Field(default=8, ge=1)
    token_estimation_safety_factor: float = Field(default=1.25, ge=1)
    context_compression_notice: str = "上下文有点长，我先整理一下记忆，稍等我几秒喵~"
    output_reasoning_content: bool = False
    pass_back_reasoning_content: bool = False
    debug_dump_messages: bool = False
    enable_deepseek_v4_roleplay_instruct: bool = False
    extra_requirements_path: str = (
        "plugins_config/ai_group_chat/prompts/deepseek_v4/extra_requirements.md"
    )
    deepseek_v4_roleplay_instruct_path: str = (
        "plugins_config/ai_group_chat/prompts/deepseek_v4/roleplay_instruct.md"
    )
    allow_mention_all: bool = False
    persist_tool_results: bool = False
    group_config: list[GroupChatConfig]

    @model_validator(mode="after")
    def check_multimodal_fallback(self) -> "AIGroupChatConfig":
        """校验非多模态主模型必须配置备用多模态模型。"""
        if self.supports_multimodal:
            return self
        if self._has_text(self.multimodal_fallback_model_name) and self._has_text(
            self.multimodal_fallback_model_vendors
        ):
            return self
        raise ValueError(
            "主模型不支持多模态时必须配置 multimodal_fallback_model_name "
            "和 multimodal_fallback_model_vendors"
        )

    def _has_text(self, value: str | None) -> bool:
        """判断可选配置字符串是否填写了有效内容。"""
        return value is not None and value.strip() != ""


def load_ai_group_chat_config() -> AIGroupChatConfig:
    """读取并校验 AI 群聊插件配置。"""
    return load_plugin_config(
        section_name=CONFIG_SECTION,
        model_cls=AIGroupChatConfig,
        config_path=CONFIG_PATH,
    )


def build_system_prompt(
    *, config: AIGroupChatConfig, group_config: GroupChatConfig
) -> str:
    """组合角色提示词、知识库和当前模型需要常驻的通用要求。"""
    system_prompt = _read_text_file(group_config.system_prompt_path)
    knowledge_base = _read_text_file(group_config.knowledge_base_path)
    prompt_parts = [system_prompt, knowledge_base]
    if not should_use_deepseek_v4_depth_zero_prompt(config=config):
        prompt_parts.append(load_extra_requirements(config=config))
    return "\n\n".join(prompt_parts)


def should_use_deepseek_v4_depth_zero_prompt(*, config: AIGroupChatConfig) -> bool:
    """判断当前模型是否应使用 DeepSeek V4 专属 Depth 0 提示词。"""
    return (
        config.enable_deepseek_v4_roleplay_instruct
        and is_deepseek_v4_roleplay_model(model_name=config.model_name)
    )


def should_use_deepseek_v4_depth_zero_prompt_for_model(
    *, config: AIGroupChatConfig, model_name: str
) -> bool:
    """判断指定模型是否应使用 DeepSeek V4 专属 Depth 0 提示词。"""
    return (
        config.enable_deepseek_v4_roleplay_instruct
        and is_deepseek_v4_roleplay_model(model_name=model_name)
    )


def is_deepseek_v4_roleplay_model(*, model_name: str) -> bool:
    """判断模型名称是否属于 DeepSeek V4 角色沉浸模型集合。"""
    return model_name in DEEPSEEK_V4_ROLEPLAY_MODELS


def load_extra_requirements(*, config: AIGroupChatConfig) -> str:
    """读取所有模型通用的群聊行为要求提示词。"""
    return _read_required_text_file(
        file_path=config.extra_requirements_path,
        description="通用群聊行为要求提示词文件",
    )


def _read_text_file(file_path: str) -> str:
    """读取 UTF-8 文本配置文件。"""
    path = Path(file_path)
    return path.read_text(encoding="utf-8")


def _read_required_text_file(*, file_path: str, description: str) -> str:
    """读取必须存在且不能为空的 UTF-8 文本配置文件。"""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"{description}不存在: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if content == "":
        raise ValueError(f"{description}为空: {path}")
    return content
