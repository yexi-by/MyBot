"""AI 群聊工具图片观察服务。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.plugins.base import Context
from app.services import ChatMessage
from app.services.llm.tools import LLMToolImageArtifact

from .config import AIGroupChatConfig


class ActiveModelLike(Protocol):
    """描述视觉观察决策所需的当前模型能力。"""

    @property
    def model_name(self) -> str:
        """模型名称。"""
        ...

    @property
    def model_vendors(self) -> str:
        """模型服务商名称。"""
        ...

    @property
    def supports_multimodal(self) -> bool:
        """是否支持多模态图片输入。"""
        ...


@dataclass(frozen=True)
class ToolImageObservation:
    """工具图片处理后追加给本轮模型的消息。"""

    working_messages: list[ChatMessage]
    history_messages: list[ChatMessage]


class ToolImageObserver:
    """按模型能力和配置处理工具返回的图片附件。"""

    def __init__(self, *, config: AIGroupChatConfig, context: Context) -> None:
        """保存配置和 LLM 访问入口。"""
        self.config: AIGroupChatConfig = config
        self.context: Context = context

    async def observe(
        self,
        *,
        artifacts: list[LLMToolImageArtifact],
        active_model: ActiveModelLike,
        source_tool_name: str,
    ) -> ToolImageObservation:
        """把工具图片转换为本轮模型可用的临时上下文。"""
        if not artifacts or self.config.tool_image_delivery_mode == "metadata_only":
            return ToolImageObservation(working_messages=[], history_messages=[])
        selected_artifacts = artifacts[: self.config.tool_image_summary_max_images]
        if (
            self.config.tool_image_delivery_mode == "auto"
            and active_model.supports_multimodal
        ):
            return self._build_direct_image_observation(
                artifacts=selected_artifacts,
                source_tool_name=source_tool_name,
            )
        return await self._build_summary_observation(
            artifacts=selected_artifacts,
            source_tool_name=source_tool_name,
        )

    def _build_direct_image_observation(
        self,
        *,
        artifacts: list[LLMToolImageArtifact],
        source_tool_name: str,
    ) -> ToolImageObservation:
        """生成直接交给多模态模型的图片消息。"""
        text = self._build_metadata_text(
            artifacts=artifacts,
            title=f"工具 {source_tool_name} 返回了图片附件",
        )
        message = ChatMessage(
            role="user",
            text=text,
            image=[artifact.image_bytes for artifact in artifacts],
        )
        history_messages = [
            ChatMessage(
                role="user",
                text=(
                    "工具图片观察：本轮已向多模态模型提供 "
                    f"{len(artifacts)} 张图片附件，长期上下文不保存图片字节。"
                ),
            )
        ]
        return ToolImageObservation(
            working_messages=[message],
            history_messages=history_messages,
        )

    async def _build_summary_observation(
        self,
        *,
        artifacts: list[LLMToolImageArtifact],
        source_tool_name: str,
    ) -> ToolImageObservation:
        """调用独立备用视觉模型生成图片摘要。"""
        fallback_model_name = self.config.multimodal_fallback_model_name
        fallback_model_vendors = self.config.multimodal_fallback_model_vendors
        if fallback_model_name is None or fallback_model_vendors is None:
            raise RuntimeError("工具图片观察需要配置多模态备用模型")
        metadata_text = self._build_metadata_text(
            artifacts=artifacts,
            title=f"工具 {source_tool_name} 返回了待观察图片",
        )
        summary_messages = [
            ChatMessage(
                role="system",
                text=self._resolve_observation_system_prompt(),
            ),
            ChatMessage(
                role="user",
                text=f"{self._resolve_observation_user_prompt()}\n\n{metadata_text}",
                image=[artifact.image_bytes for artifact in artifacts],
            ),
        ]
        summary = await self.context.llm.get_ai_text_response(
            messages=summary_messages,
            model_vendors=fallback_model_vendors,
            model_name=fallback_model_name,
        )
        observation_text = (
            "工具图片观察结果（仅供本轮回复参考，不代表用户新发言）：\n"
            f"{summary.strip()}"
        )
        message = ChatMessage(role="user", text=observation_text)
        return ToolImageObservation(
            working_messages=[message],
            history_messages=[message],
        )

    def _resolve_observation_system_prompt(self) -> str:
        """读取视觉摘要 system prompt。"""
        return self._resolve_prompt_file(
            path_value=self.config.tool_image_observation_system_prompt_path,
            field_name="tool_image_observation_system_prompt_path",
        )

    def _resolve_observation_user_prompt(self) -> str:
        """读取视觉摘要 user prompt。"""
        return self._resolve_prompt_file(
            path_value=self.config.tool_image_observation_user_prompt_path,
            field_name="tool_image_observation_user_prompt_path",
        )

    def _resolve_prompt_file(
        self,
        *,
        path_value: str | None,
        field_name: str,
    ) -> str:
        """解析显式配置的提示词文件。"""
        if path_value is None or path_value.strip() == "":
            raise ValueError(f"{field_name} 必须配置为提示词文件路径")
        path = Path(path_value)
        if not path.is_file():
            raise FileNotFoundError(f"{field_name} 不存在: {path}")
        content = path.read_text(encoding="utf-8").strip()
        if content == "":
            raise ValueError(f"{field_name} 为空: {path}")
        return content

    def _build_metadata_text(
        self, *, artifacts: list[LLMToolImageArtifact], title: str
    ) -> str:
        """把图片附件元信息整理为简短文本。"""
        lines = [title, ""]
        for index, artifact in enumerate(artifacts, start=1):
            metadata_text = json.dumps(
                artifact.metadata,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            lines.append(f"{index}. {artifact.label}")
            lines.append(f"   元信息: {metadata_text}")
        return "\n".join(lines)
