"""DeepSeek V4 角色沉浸 Depth 0 提示词构造。"""

from dataclasses import dataclass
from pathlib import Path

from app.services import ChatMessage

from .config import AIGroupChatConfig, load_extra_requirements


@dataclass(frozen=True)
class DeepSeekV4PromptPack:
    """保存 DeepSeek V4 每次正式请求都要临时追加的提示词。"""

    extra_requirements: str
    roleplay_instruct: str
    extra_requirements_path: Path
    roleplay_instruct_path: Path

    def build_depth_zero_message(self) -> ChatMessage:
        """构造不进入长期上下文的 Depth 0 user message。"""
        text = (
            "<其他需求>\n"
            f"{self.extra_requirements}\n"
            "</其他需求>\n\n"
            "<角色沉浸式扮演需求>\n"
            f"{self.roleplay_instruct}\n"
            "</角色沉浸式扮演需求>"
        )
        return ChatMessage(
            role="user",
            text=text,
        )


def load_deepseek_v4_prompt_pack(*, config: AIGroupChatConfig) -> DeepSeekV4PromptPack:
    """按插件配置读取 DeepSeek V4 Depth 0 提示词文件。"""
    extra_requirements_path = Path(config.extra_requirements_path)
    roleplay_instruct_path = Path(config.deepseek_v4_roleplay_instruct_path)
    return DeepSeekV4PromptPack(
        extra_requirements=load_extra_requirements(config=config),
        roleplay_instruct=_read_prompt_file(path=roleplay_instruct_path),
        extra_requirements_path=extra_requirements_path,
        roleplay_instruct_path=roleplay_instruct_path,
    )


def _read_prompt_file(*, path: Path) -> str:
    """读取提示词文件，缺失或空文件都立即显式报错。"""
    if not path.is_file():
        raise FileNotFoundError(f"DeepSeek V4 提示词文件不存在: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if content == "":
        raise ValueError(f"DeepSeek V4 提示词文件为空: {path}")
    return content
