"""AI 群聊内部多模态视觉描述工具。"""

from dataclasses import dataclass, field
from hashlib import sha256

from app.config import MaterializedAIGroupChatConfig
from app.models import StrictModel
from app.plugins.base import Context
from app.services import ChatMessage
from app.services.llm.tools import LLMImageArtifact, LLMImageError, LLMImageItem
from app.utils.log import log_event

class VisionDescriptionResult(StrictModel):
    """描述内部视觉工具生成的结构化结果。"""

    ok: bool
    is_error: bool
    description: str | None
    observed_count: int
    truncated_count: int
    errors: list[LLMImageError]
    message: str


@dataclass
class VisionTurnState:
    """保存单轮对话内可复用的视觉描述。"""

    delivered_image_keys: set[str] = field(default_factory=set)
    consumed_image_slots: int = 0


@dataclass(frozen=True)
class VisionDelivery:
    """描述图片处理后提供给主模型和长期上下文的消息。"""

    working_messages: list[ChatMessage]
    history_messages: list[ChatMessage]
    result: VisionDescriptionResult | None


class VisionDescriptionTool:
    """把图片直接交给主模型，或调用独立视觉模型生成文字描述。"""

    def __init__(
        self, *, config: MaterializedAIGroupChatConfig, context: Context
    ) -> None:
        """保存视觉工具配置与 LLM 访问入口。"""
        self.config = config
        self.context: Context = context

    async def deliver(
        self,
        *,
        items: list[LLMImageItem],
        truncated_count: int,
        question: str,
        source_name: str,
        turn_state: VisionTurnState,
    ) -> VisionDelivery:
        """按主模型能力生成直接图片消息或文字观察消息。"""
        supplied_artifact_count = sum(
            isinstance(item, LLMImageArtifact) for item in items
        )
        unique_items: list[LLMImageItem] = []
        pending_image_keys: set[str] = set()
        duplicate_count = 0
        for item in items:
            if not isinstance(item, LLMImageArtifact):
                unique_items.append(item)
                continue
            image_key = self._build_image_key(artifact=item)
            if (
                image_key in turn_state.delivered_image_keys
                or image_key in pending_image_keys
            ):
                duplicate_count += 1
                continue
            pending_image_keys.add(image_key)
            unique_items.append(item)
        if duplicate_count > 0:
            log_event(
                level="DEBUG",
                event="ai_group_chat.vision.duplicate_skipped",
                category="plugin",
                message="本轮已处理相同图片内容，已跳过重复图片",
                source_name=source_name,
                duplicate_count=duplicate_count,
            )
        if not unique_items and truncated_count == 0:
            return VisionDelivery([], [], None)
        remaining_slots = max(
            0,
            self.config.source.images.max_per_turn - turn_state.consumed_image_slots,
        )
        selected_items = unique_items[:remaining_slots]
        selected_artifacts = [
            item for item in selected_items if isinstance(item, LLMImageArtifact)
        ]
        selected_errors = [
            item for item in selected_items if isinstance(item, LLMImageError)
        ]
        consumed_count = len(selected_items)
        turn_state.consumed_image_slots += consumed_count
        total_truncated_count = truncated_count + max(
            0,
            len(unique_items) - consumed_count,
        )
        log_event(
            level="DEBUG" if total_truncated_count == 0 else "WARNING",
            event="ai_group_chat.vision.delivery_prepared",
            category="plugin",
            message="AI 群聊图片传递范围已确定",
            source_name=source_name,
            supplied_image_count=supplied_artifact_count,
            duplicate_image_count=duplicate_count,
            supplied_error_count=sum(
                isinstance(item, LLMImageError) for item in items
            ),
            observed_image_count=len(selected_artifacts),
            retained_error_count=len(selected_errors),
            truncated_count=total_truncated_count,
            consumed_image_slots=turn_state.consumed_image_slots,
            max_image_slots=self.config.source.images.max_per_turn,
        )
        if self.config.source.model.supports_images:
            delivery = self._build_direct_delivery(
                artifacts=selected_artifacts,
                errors=selected_errors,
                truncated_count=total_truncated_count,
                source_name=source_name,
            )
        else:
            delivery = await self._build_description_delivery(
                artifacts=selected_artifacts,
                errors=selected_errors,
                truncated_count=total_truncated_count,
                question=question,
                source_name=source_name,
            )
        if delivery.working_messages:
            turn_state.delivered_image_keys.update(
                self._build_image_key(artifact=artifact)
                for artifact in selected_artifacts
            )
        return delivery

    def _build_direct_delivery(
        self,
        *,
        artifacts: list[LLMImageArtifact],
        errors: list[LLMImageError],
        truncated_count: int,
        source_name: str,
    ) -> VisionDelivery:
        """把成功图片直接附加给支持多模态的主模型。"""
        if not artifacts:
            result = self._build_unavailable_result(
                errors=errors,
                truncated_count=truncated_count,
            )
            if result is None:
                return VisionDelivery([], [], None)
            message = self._build_result_message(result=result, source_name=source_name)
            return VisionDelivery([message], [], result)
        result = VisionDescriptionResult(
            ok=True,
            is_error=False,
            description=None,
            observed_count=len(artifacts),
            truncated_count=truncated_count,
            errors=errors,
            message="图片已直接提供给支持多模态的主模型。",
        )
        message = ChatMessage(
            role="user",
            text=self._build_direct_metadata_text(
                artifacts=artifacts,
                errors=errors,
                truncated_count=truncated_count,
                source_name=source_name,
            ),
            image=[artifact.image_bytes for artifact in artifacts],
        )
        return VisionDelivery([message], [], result)

    async def _build_description_delivery(
        self,
        *,
        artifacts: list[LLMImageArtifact],
        errors: list[LLMImageError],
        truncated_count: int,
        question: str,
        source_name: str,
    ) -> VisionDelivery:
        """调用隔离的多模态模型，把图片转换成主模型可读文字。"""
        if not artifacts:
            result = self._build_unavailable_result(
                errors=errors,
                truncated_count=truncated_count,
            )
            if result is None:
                return VisionDelivery([], [], None)
            message = self._build_result_message(result=result, source_name=source_name)
            return VisionDelivery(
                working_messages=[message],
                history_messages=self._history_messages(
                    message=message,
                    result=result,
                ),
                result=result,
            )

        vision_errors = list(errors)
        try:
            description = await self._request_description(
                artifacts=artifacts,
                question=question,
                source_name=source_name,
            )
        except Exception as exc:
            vision_errors.append(
                LLMImageError(
                    label=source_name,
                    error_type=type(exc).__name__,
                    error=f"视觉模型请求失败: {exc}",
                )
            )
            result = VisionDescriptionResult(
                ok=False,
                is_error=True,
                description=None,
                observed_count=0,
                truncated_count=truncated_count,
                errors=vision_errors,
                message="视觉工具调用失败，主模型只能依据文字和错误信息回答。",
            )
            message = self._build_result_message(
                result=result,
                source_name=source_name,
            )
            return VisionDelivery(
                working_messages=[message],
                history_messages=self._history_messages(
                    message=message,
                    result=result,
                ),
                result=result,
            )

        result = VisionDescriptionResult(
            ok=True,
            is_error=False,
            description=description,
            observed_count=len(artifacts),
            truncated_count=truncated_count,
            errors=vision_errors,
            message=(
                "视觉描述已生成。"
                if not vision_errors and truncated_count == 0
                else "视觉描述已生成，部分图片未读取或已按上限截断。"
            ),
        )
        message = self._build_result_message(result=result, source_name=source_name)
        return VisionDelivery(
            working_messages=[message],
            history_messages=self._history_messages(
                message=message,
                result=result,
            ),
            result=result,
        )

    async def _request_description(
        self,
        *,
        artifacts: list[LLMImageArtifact],
        question: str,
        source_name: str,
    ) -> str:
        """发起不携带群聊角色、历史或工具的独立视觉请求。"""
        vision = self.config.source.vision
        if vision is None:
            raise RuntimeError("独立视觉模型配置缺失")
        system_prompt = self.config.vision_system_prompt
        user_prompt = self.config.vision_user_prompt
        if system_prompt is None or user_prompt is None:
            raise RuntimeError("独立视觉提示词尚未加载")
        labels = "\n".join(
            f"{index}. {artifact.label}"
            for index, artifact in enumerate(artifacts, start=1)
        )
        prompt = "\n\n".join(
            [
                user_prompt,
                f"图片来源：{source_name}",
                f"当前问题：\n{question.strip() or '（当前消息没有文字问题）'}",
                f"图片顺序：\n{labels}",
            ]
        )
        messages = [
            ChatMessage(
                role="system",
                text=system_prompt,
            ),
            ChatMessage(
                role="user",
                text=prompt,
                image=[artifact.image_bytes for artifact in artifacts],
            ),
        ]
        response = await self.context.llm.get_ai_text_response(
            messages=messages,
            provider=vision.model.provider,
            model_name=vision.model.name,
            max_attempts=vision.max_attempts,
            retry_delay_seconds=vision.retry_delay_seconds,
        )
        description = response.strip()
        if description == "":
            raise ValueError("视觉模型返回了空描述")
        return description

    def _build_unavailable_result(
        self,
        *,
        errors: list[LLMImageError],
        truncated_count: int,
    ) -> VisionDescriptionResult | None:
        """没有成功图片时生成可恢复结果。"""
        if not errors and truncated_count == 0:
            return None
        return VisionDescriptionResult(
            ok=False,
            is_error=True,
            description=None,
            observed_count=0,
            truncated_count=truncated_count,
            errors=errors,
            message="图片内容不可用，主模型只能依据文字和错误信息回答。",
        )

    def _build_result_message(
        self, *, result: VisionDescriptionResult, source_name: str
    ) -> ChatMessage:
        """把视觉结果整理为明确标注来源的模型观察消息。"""
        lines = [
            "视觉工具观察结果（系统生成，不是用户原话，只作为事实参考）：",
            f"来源：{source_name}",
            f"状态：{result.message}",
        ]
        if result.description is not None:
            lines.extend(["", result.description])
        if result.truncated_count > 0:
            lines.append(f"未观察图片数：{result.truncated_count}")
        if result.errors:
            lines.extend(["", "图片错误："])
            lines.extend(
                f"- {error.label}: {error.error_type}: {error.error}"
                for error in result.errors
            )
        return ChatMessage(role="user", text="\n".join(lines))

    def _build_direct_metadata_text(
        self,
        *,
        artifacts: list[LLMImageArtifact],
        errors: list[LLMImageError],
        truncated_count: int,
        source_name: str,
    ) -> str:
        """生成不暴露本地路径或原始 URL 的图片附件说明。"""
        lines = [f"{source_name}包含以下图片附件："]
        lines.extend(
            f"{index}. {artifact.label}"
            for index, artifact in enumerate(artifacts, start=1)
        )
        if truncated_count > 0:
            lines.append(f"另有 {truncated_count} 张图片因数量上限未附带。")
        if errors:
            lines.append("部分图片读取失败：")
            lines.extend(
                f"- {error.label}: {error.error_type}: {error.error}"
                for error in errors
            )
        return "\n".join(lines)

    def _history_messages(
        self,
        *,
        message: ChatMessage,
        result: VisionDescriptionResult,
    ) -> list[ChatMessage]:
        """只按配置保存成功生成的视觉描述，不保存临时读取错误。"""
        if (
            self.config.source.vision is None
            or not self.config.source.vision.retain_descriptions
            or result.description is None
        ):
            return []
        return [message]

    def _build_image_key(self, *, artifact: LLMImageArtifact) -> str:
        """按单张图片内容构造单轮去重键。"""
        digest = sha256()
        digest.update(len(artifact.image_bytes).to_bytes(8, byteorder="big"))
        digest.update(artifact.image_bytes)
        return digest.hexdigest()
