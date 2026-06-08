"""合并转发图片自动补取逻辑。"""

from app.models import JsonObject, JsonValue
from app.services import CompositeToolExecutor
from app.services.llm.schemas import LLMToolCall
from app.services.llm.tools import LLMToolExecutionResult
from app.utils.log import log_event

from .config import AIGroupChatConfig

FORWARD_MESSAGE_TOOL_NAME = "qq__get_forward_message"
FORWARD_MESSAGE_IMAGES_TOOL_NAME = "qq__get_forward_message_images"


class ForwardImageAutoFetcher:
    """在模型只读取合并转发时，按需补取其中图片附件。"""

    def __init__(self, *, config: AIGroupChatConfig) -> None:
        """保存自动补取所需配置。"""
        self.config: AIGroupChatConfig = config

    def should_fetch(
        self,
        *,
        tool_call: LLMToolCall,
        result: JsonValue,
        explicit_forward_image_call: bool,
    ) -> bool:
        """判断是否需要在读取合并转发后自动补取图片内容。"""
        if tool_call.name != FORWARD_MESSAGE_TOOL_NAME:
            return False
        if explicit_forward_image_call:
            return False
        if not self.config.forward_image_tool_enabled:
            return False
        if self.config.tool_image_delivery_mode == "metadata_only":
            return False
        if not isinstance(result, dict):
            return False
        if result.get("ok") is not True:
            return False
        image_count = result.get("image_count")
        if isinstance(image_count, int):
            return image_count > 0
        return self._forward_result_has_images(result=result)

    async def fetch(
        self,
        *,
        forward_result: JsonValue,
        tool_executor: CompositeToolExecutor,
        group_id: str,
    ) -> LLMToolExecutionResult:
        """补取合并转发中的图片附件。"""
        if not isinstance(forward_result, dict):
            return LLMToolExecutionResult(result=self._build_error_result())
        message_id = forward_result.get("message_id")
        if not isinstance(message_id, str) or message_id.strip() == "":
            return LLMToolExecutionResult(
                result=self._build_error_result(
                    error="合并转发工具结果缺少 message_id",
                )
            )
        arguments: JsonObject = {"message_id": message_id, "mode": "all"}
        log_event(
            level="DEBUG",
            event="ai_group_chat.forward_images.auto_fetch.start",
            category="plugin",
            message="检测到合并转发包含图片，自动补取图片内容",
            group_id=group_id,
            message_id=message_id,
            tool_name=FORWARD_MESSAGE_IMAGES_TOOL_NAME,
            arguments=arguments,
        )
        try:
            execution_result = await tool_executor.call_tool_with_artifacts(
                name=FORWARD_MESSAGE_IMAGES_TOOL_NAME,
                arguments=arguments,
            )
        except Exception as exc:
            result = self._build_error_result(
                error_type=type(exc).__name__,
                error=str(exc),
            )
            log_event(
                level="WARNING",
                event="ai_group_chat.forward_images.auto_fetch.failed",
                category="plugin",
                message="合并转发图片自动补取失败",
                group_id=group_id,
                message_id=message_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return LLMToolExecutionResult(result=result)
        log_event(
            level="DEBUG",
            event="ai_group_chat.forward_images.auto_fetch.finished",
            category="plugin",
            message="合并转发图片自动补取完成",
            group_id=group_id,
            message_id=message_id,
            image_artifacts_count=len(execution_result.image_artifacts),
        )
        return execution_result

    def merge_result(
        self, *, forward_result: JsonValue, image_result: JsonValue
    ) -> JsonValue:
        """把自动补取图片结果合并进原合并转发工具结果。"""
        if not isinstance(forward_result, dict):
            return forward_result
        merged_result: JsonObject = dict(forward_result)
        merged_result["auto_image_fetch"] = image_result
        return merged_result

    def _forward_result_has_images(self, *, result: JsonObject) -> bool:
        """兼容旧工具结果，递归判断合并转发结果里是否包含图片段。"""
        messages = result.get("messages")
        if not isinstance(messages, list):
            return False
        for message in messages:
            if not isinstance(message, dict):
                continue
            raw_image_count = message.get("image_count")
            if isinstance(raw_image_count, int) and raw_image_count > 0:
                return True
            segment_types = message.get("segment_types")
            if isinstance(segment_types, list) and "image" in segment_types:
                return True
            nested_forwards = message.get("nested_forwards")
            if not isinstance(nested_forwards, list):
                continue
            for nested_forward in nested_forwards:
                if isinstance(nested_forward, dict) and self._forward_result_has_images(
                    result=nested_forward
                ):
                    return True
        return False

    def _build_error_result(
        self,
        *,
        error_type: str = "ForwardImageAutoFetchFailed",
        error: str = "合并转发图片自动补取失败",
    ) -> JsonObject:
        """构造图片自动补取失败时的模型可读结果。"""
        return {
            "ok": False,
            "is_error": True,
            "action": "auto_get_forward_message_images",
            "error_type": error_type,
            "error": error,
            "message": "合并转发图片自动补取失败，可尝试手动调用 qq__get_forward_message_images。",
        }
