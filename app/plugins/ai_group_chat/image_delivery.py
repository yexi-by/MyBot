"""校验群聊图片附件并将原始字节直接交给主模型。"""

from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from typing import Literal

from PIL import Image as PillowImage
from PIL import UnidentifiedImageError

from app.config import AIImageConfig
from app.models import StrictModel
from app.services import ChatMessage
from app.services.llm.tools import LLMImageArtifact, LLMImageError, LLMImageItem
from app.utils.file_type import detect_mime_type
from app.utils.log import log_event


class ImageDeliveryError(ValueError):
    """图片超过明确配置的限制，需要结束本轮并反馈。"""


class ImageDeliveryResult(StrictModel):
    """图片附件的交付数量与部分读取错误。"""

    ok: bool
    is_error: bool
    observed_count: int
    truncated_count: int
    errors: list[LLMImageError]
    message: str


@dataclass
class ImageTurnState:
    """记录当前轮已交付图片，防止重复附带和超出总额。"""

    delivered_image_keys: set[str] = field(default_factory=set)
    consumed_image_slots: int = 0
    consumed_image_bytes: int = 0


@dataclass(frozen=True)
class ImageDelivery:
    """提供给本轮请求以及可选长期上下文的图片消息。"""

    working_messages: list[ChatMessage]
    history_messages: list[ChatMessage]
    result: ImageDeliveryResult | None


class ImageDeliveryService:
    """按图片限制校验附件，不调用模型或转换图片内容。"""

    def __init__(self, *, config: AIImageConfig) -> None:
        self.config = config

    def deliver(
        self,
        *,
        items: list[LLMImageItem],
        truncated_count: int,
        source_name: str,
        turn_state: ImageTurnState,
    ) -> ImageDelivery:
        """去重、校验额度，并直接附带成功图片和结构化部分错误。"""
        artifacts: list[LLMImageArtifact] = []
        errors: list[LLMImageError] = []
        pending_keys: set[str] = set()
        duplicate_count = 0
        for item in items:
            if isinstance(item, LLMImageError):
                if self.config.oversize_behavior == "error" and item.error_type == "ImageReadTooLargeError":
                    raise ImageDeliveryError(f"{item.label}: {item.error}")
                errors.append(item)
                continue
            error = self._validate_artifact(artifact=item)
            if error is not None:
                if self.config.oversize_behavior == "error":
                    raise ImageDeliveryError(f"{error.label}: {error.error}")
                errors.append(error)
                continue
            image_key = self._build_image_key(artifact=item)
            if image_key in turn_state.delivered_image_keys or image_key in pending_keys:
                duplicate_count += 1
                continue
            pending_keys.add(image_key)
            artifacts.append(item)

        if not artifacts and not errors and truncated_count == 0:
            return ImageDelivery([], [], None)
        limit = self.config.max_per_turn
        remaining = max(0, limit - turn_state.consumed_image_slots) if limit else None
        selected, total_byte_errors = self._apply_turn_limits(
            artifacts=artifacts,
            consumed_bytes=turn_state.consumed_image_bytes,
            remaining_slots=remaining,
        )
        errors.extend(total_byte_errors)
        truncated_count += len(artifacts) - len(selected)
        turn_state.consumed_image_slots += len(selected)
        turn_state.consumed_image_bytes += sum(len(item.image_bytes) for item in selected)
        turn_state.delivered_image_keys.update(self._build_image_key(artifact=item) for item in selected)
        result = ImageDeliveryResult(
            ok=bool(selected), is_error=not selected,
            observed_count=len(selected), truncated_count=truncated_count, errors=errors,
            message="图片已直接附带给主模型。" if selected else "图片附件不可用。",
        )
        message = ChatMessage(
            role="user",
            text=self._build_direct_metadata_text(
                artifacts=selected, errors=errors,
                truncated_count=truncated_count, source_name=source_name,
            ),
            image=[item.image_bytes for item in selected] or None,
            image_detail=self._request_image_detail() if selected else None,
        )
        log_event(
            level="WARNING" if errors or truncated_count else "DEBUG",
            event="ai_group_chat.images.delivered", category="plugin",
            message="AI 群聊图片附件已整理", source_name=source_name,
            observed_image_count=len(selected), duplicate_image_count=duplicate_count,
            error_count=len(errors), truncated_count=truncated_count,
            consumed_image_slots=turn_state.consumed_image_slots,
            consumed_image_bytes=turn_state.consumed_image_bytes,
        )
        return ImageDelivery(
            [message], [message] if selected and self.config.retain_images else [], result,
        )

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
            lines.append(f"另有 {truncated_count} 张图片因配置限制未附带。")
        if errors:
            lines.append("部分图片读取失败：")
            lines.extend(
                f"- {error.label}: {error.error_type}: {error.error}"
                for error in errors
            )
        return "\n".join(lines)

    def _validate_artifact(
        self, *, artifact: LLMImageArtifact
    ) -> LLMImageError | None:
        """检查工具附件也遵守 AIChat 图片配置，而不只检查 NapCat 下载。"""
        image_config = self.config
        image_bytes = artifact.image_bytes
        if (
            image_config.max_image_bytes > 0
            and len(image_bytes) > image_config.max_image_bytes
        ):
            return LLMImageError(
                label=artifact.label,
                error_type="ImageReadTooLargeError",
                error=(
                    f"图片大小 {len(image_bytes)} 字节超过配置上限 "
                    f"{image_config.max_image_bytes} 字节"
                ),
            )
        if image_config.allowed_mime_types:
            try:
                mime_type = detect_mime_type(image_bytes)
            except ValueError as exc:
                return LLMImageError(
                    label=artifact.label,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            if mime_type not in image_config.allowed_mime_types:
                return LLMImageError(
                    label=artifact.label,
                    error_type="UnsupportedImageType",
                    error=f"图片格式 {mime_type} 不在配置允许列表中",
                )
        if image_config.max_width == 0 and image_config.max_height == 0:
            return None
        try:
            with PillowImage.open(BytesIO(image_bytes)) as image:
                width, height = image.size
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            return LLMImageError(
                label=artifact.label,
                error_type=type(exc).__name__,
                error=f"无法读取图片尺寸: {exc}",
            )
        if image_config.max_width > 0 and width > image_config.max_width:
            return LLMImageError(
                label=artifact.label,
                error_type="ImageDimensionsExceeded",
                error=f"图片宽度 {width} 超过配置上限 {image_config.max_width}",
            )
        if image_config.max_height > 0 and height > image_config.max_height:
            return LLMImageError(
                label=artifact.label,
                error_type="ImageDimensionsExceeded",
                error=f"图片高度 {height} 超过配置上限 {image_config.max_height}",
            )
        return None

    def _apply_turn_limits(
        self,
        *,
        artifacts: list[LLMImageArtifact],
        consumed_bytes: int,
        remaining_slots: int | None,
    ) -> tuple[list[LLMImageArtifact], list[LLMImageError]]:
        """逐张检查总字节，仅成功交付的图片占用剩余数量额度。"""
        total_limit = self.config.max_total_bytes_per_request
        selected: list[LLMImageArtifact] = []
        errors: list[LLMImageError] = []
        current_bytes = consumed_bytes
        for artifact in artifacts:
            if remaining_slots is not None and len(selected) >= remaining_slots:
                break
            next_bytes = current_bytes + len(artifact.image_bytes)
            if total_limit == 0 or next_bytes <= total_limit:
                selected.append(artifact)
                current_bytes = next_bytes
                continue
            error = LLMImageError(
                label=artifact.label, error_type="ImageRequestBytesExceeded",
                error=f"加入该图片后本轮图片总字节为 {next_bytes}，超过配置上限 {total_limit}",
            )
            if self.config.oversize_behavior == "error":
                raise ImageDeliveryError(f"{error.label}: {error.error}")
            errors.append(error)
        return selected, errors

    def _build_image_key(self, *, artifact: LLMImageArtifact) -> str:
        """按单张图片内容构造单轮去重键。"""
        digest = sha256()
        digest.update(len(artifact.image_bytes).to_bytes(8, byteorder="big"))
        digest.update(artifact.image_bytes)
        return digest.hexdigest()

    def _request_image_detail(
        self,
    ) -> Literal["auto", "low", "high"] | None:
        """把显式 omit 配置转换成协议层的不发送字段。"""
        detail = self.config.image_detail
        return None if detail == "omit" else detail
