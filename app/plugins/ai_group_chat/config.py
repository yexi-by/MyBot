"""AI 群聊插件配置模型和提示词加载。"""

from pathlib import Path

from pydantic import Field, model_validator

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
    vision_model_name: str | None = None
    vision_model_vendors: str | None = None
    vision_system_prompt_path: str | None = None
    vision_user_prompt_path: str | None = None
    vision_request_retry_count: int = Field(default=5, ge=1, le=10)
    vision_request_retry_delay_seconds: float = Field(default=0.25, gt=0, le=10)
    image_delivery_max_images: int = Field(default=20, ge=1, le=20)
    image_fetch_concurrency: int = Field(default=16, ge=1, le=32)
    image_download_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    persist_vision_descriptions: bool = True
    max_tool_rounds: int = Field(default=16, ge=1)
    token_estimation_safety_factor: float = Field(default=1.05, ge=1)
    context_compression_notice: str = "上下文有点长，我先整理一下记忆，稍等我几秒喵~"
    max_reply_chars: int = Field(default=1000, ge=1)
    output_reasoning_content: bool = False
    pass_back_reasoning_content: bool = False
    debug_dump_messages: bool = False
    extra_requirements_path: str = (
        "plugins_config/ai_group_chat/prompts/extra_requirements.md"
    )
    allow_mention_all: bool = False
    persist_tool_results: bool = False
    forward_image_tool_enabled: bool = True
    forward_image_max_images_per_call: int = Field(default=20, ge=1, le=20)
    forward_image_max_all_images: int = Field(default=50, ge=1, le=50)
    group_config: list[GroupChatConfig]

    @model_validator(mode="after")
    def check_vision_config(self) -> "AIGroupChatConfig":
        """校验主模型能力与独立视觉工具配置是否一致。"""
        vision_fields = {
            "vision_model_name": self.vision_model_name,
            "vision_model_vendors": self.vision_model_vendors,
            "vision_system_prompt_path": self.vision_system_prompt_path,
            "vision_user_prompt_path": self.vision_user_prompt_path,
        }
        if self.supports_multimodal:
            configured_fields = [
                name for name, value in vision_fields.items() if self._has_text(value)
            ]
            configured_fields.extend(
                field_name
                for field_name in (
                    "vision_request_retry_count",
                    "vision_request_retry_delay_seconds",
                )
                if field_name in self.model_fields_set
            )
            if configured_fields:
                raise ValueError(
                    "主模型支持多模态时不应配置独立视觉模型字段: "
                    + ", ".join(configured_fields)
                )
            return self
        missing_fields = [
            name for name, value in vision_fields.items() if not self._has_text(value)
        ]
        if missing_fields:
            raise ValueError(
                "主模型不支持多模态时必须配置: " + ", ".join(missing_fields)
            )
        self._check_vision_prompt_files()
        return self

    def _has_text(self, value: str | None) -> bool:
        """判断可选配置字符串是否填写了有效内容。"""
        return value is not None and value.strip() != ""

    def _check_vision_prompt_files(self) -> None:
        """视觉工具启用时，提示词必须来自显式配置的非空文件。"""
        self._check_vision_prompt_file(
            file_path=self.vision_system_prompt_path,
            field_name="vision_system_prompt_path",
        )
        self._check_vision_prompt_file(
            file_path=self.vision_user_prompt_path,
            field_name="vision_user_prompt_path",
        )

    def _check_vision_prompt_file(
        self, *, file_path: str | None, field_name: str
    ) -> None:
        """校验视觉摘要提示词文件路径存在且内容非空。"""
        if file_path is None or file_path.strip() == "":
            raise ValueError(f"{field_name} 必须配置为提示词文件路径")
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"{field_name} 不存在: {path}")
        if path.read_text(encoding="utf-8").strip() == "":
            raise ValueError(f"{field_name} 为空: {path}")


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
    """组合角色提示词、知识库和所有模型共用的群聊要求。"""
    system_prompt = _read_text_file(group_config.system_prompt_path)
    knowledge_base = _read_text_file(group_config.knowledge_base_path)
    prompt_parts = [
        system_prompt,
        knowledge_base,
        load_extra_requirements(config=config),
    ]
    return "\n\n".join(prompt_parts)


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
