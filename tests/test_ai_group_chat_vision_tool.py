"""AI 群聊内部视觉描述工具测试。"""

import unittest
from typing import cast

from app.plugins.ai_group_chat.config import AIGroupChatConfig
from app.plugins.ai_group_chat.vision_tool import (
    VisionDescriptionTool,
    VisionTurnState,
)
from app.plugins.base import Context
from app.services import ChatMessage
from app.services.llm.tools import LLMImageArtifact, LLMImageError

VISION_SYSTEM_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/system.md"
VISION_USER_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/user.md"


class RecordingVisionLLM:
    """记录视觉纯文本请求并返回固定描述。"""

    def __init__(self, *, failure: Exception | None = None) -> None:
        """保存可选失败。"""
        self.failure: Exception | None = failure
        self.requests: list[list[ChatMessage]] = []
        self.models: list[tuple[str, str]] = []
        self.retry_settings: list[tuple[int | None, float | None]] = []

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        retry_count: int | None = None,
        retry_delay: float | None = None,
    ) -> str:
        """记录隔离请求并返回描述或抛出异常。"""
        self.requests.append(messages[:])
        self.models.append((model_vendors, model_name))
        self.retry_settings.append((retry_count, retry_delay))
        if self.failure is not None:
            raise self.failure
        return "第一张是红色按钮，第二张显示成功提示。"


class VisionContext:
    """只提供视觉工具消费的 LLM。"""

    def __init__(self, llm: RecordingVisionLLM) -> None:
        """保存 LLM。"""
        self.llm = llm


def build_config(
    *,
    supports_multimodal: bool = False,
    persist_vision_descriptions: bool = True,
    image_delivery_max_images: int = 6,
) -> AIGroupChatConfig:
    """按能力构造视觉配置。"""
    values: dict[str, object] = {
        "model_name": "main-model",
        "model_vendors": "main-vendor",
        "supports_multimodal": supports_multimodal,
        "persist_vision_descriptions": persist_vision_descriptions,
        "image_delivery_max_images": image_delivery_max_images,
        "group_config": [],
    }
    if not supports_multimodal:
        values.update(
            {
                "vision_model_name": "vision-model",
                "vision_model_vendors": "vision-vendor",
                "vision_system_prompt_path": VISION_SYSTEM_PROMPT_PATH,
                "vision_user_prompt_path": VISION_USER_PROMPT_PATH,
            }
        )
    return AIGroupChatConfig.model_validate(values)


def artifact(label: str, content: bytes) -> LLMImageArtifact:
    """构造来源无关的内部图片附件。"""
    return LLMImageArtifact(
        label=label,
        image_bytes=content,
    )


def build_tool(
    *, config: AIGroupChatConfig, llm: RecordingVisionLLM
) -> VisionDescriptionTool:
    """构造视觉工具。"""
    return VisionDescriptionTool(
        config=config,
        context=cast(Context, VisionContext(llm)),
    )


class VisionDescriptionToolTest(unittest.IsolatedAsyncioTestCase):
    """验证直接图片、文字描述、错误、持久化和单轮上限。"""

    async def test_text_model_gets_strict_partial_success_result(self) -> None:
        """部分成功保留描述、错误、观察数和截断数。"""
        llm = RecordingVisionLLM()
        tool = build_tool(config=build_config(), llm=llm)

        delivery = await tool.deliver(
            items=[
                artifact("当前消息第 1 张图片", b"current"),
                artifact("引用消息第 1 张图片", b"quoted"),
                LLMImageError(
                    label="引用消息第 2 张图片",
                    error_type="ReadTimeout",
                    error="下载超时",
                )
            ],
            truncated_count=1,
            question="按钮操作成功了吗？",
            source_name="当前消息和引用消息",
            turn_state=VisionTurnState(),
        )

        self.assertIsNotNone(delivery.result)
        if delivery.result is None:
            raise AssertionError("应返回结构化视觉结果")
        self.assertEqual(
            set(delivery.result.model_dump()),
            {
                "ok",
                "is_error",
                "description",
                "observed_count",
                "truncated_count",
                "errors",
                "message",
            },
        )
        self.assertTrue(delivery.result.ok)
        self.assertFalse(delivery.result.is_error)
        self.assertEqual(delivery.result.observed_count, 2)
        self.assertEqual(delivery.result.truncated_count, 1)
        self.assertEqual(len(delivery.result.errors), 1)
        self.assertEqual(llm.models, [("vision-vendor", "vision-model")])
        self.assertEqual(llm.retry_settings, [(5, 0.25)])
        request_text = "\n".join(
            message.text or "" for message in llm.requests[0]
        )
        self.assertIn("按钮操作成功了吗？", request_text)
        self.assertIn("忽略", request_text)
        self.assertNotIn("下载超时", request_text)
        self.assertNotIn("未观察的图片数", request_text)
        self.assertEqual(llm.requests[0][1].image, [b"current", b"quoted"])
        self.assertEqual(len(delivery.working_messages), 1)
        self.assertEqual(delivery.history_messages, delivery.working_messages)
        self.assertIn(
            "系统生成，不是用户原话",
            delivery.working_messages[0].text or "",
        )

    async def test_all_image_reads_failed_is_recoverable(self) -> None:
        """没有成功图片时不请求视觉模型，并把错误交给主模型。"""
        llm = RecordingVisionLLM()
        tool = build_tool(config=build_config(), llm=llm)

        delivery = await tool.deliver(
            items=[
                LLMImageError(
                    label="当前消息第 1 张图片",
                    error_type="NapCatActionFailed",
                    error="图片不存在",
                )
            ],
            truncated_count=0,
            question="这是什么？",
            source_name="当前消息",
            turn_state=VisionTurnState(),
        )

        self.assertEqual(llm.requests, [])
        self.assertIsNotNone(delivery.result)
        if delivery.result is None:
            raise AssertionError("应返回结构化失败结果")
        self.assertFalse(delivery.result.ok)
        self.assertTrue(delivery.result.is_error)
        self.assertEqual(delivery.result.observed_count, 0)
        self.assertIn("图片不存在", delivery.working_messages[0].text or "")
        self.assertEqual(delivery.history_messages, [])

    async def test_vision_request_failure_is_recoverable(self) -> None:
        """视觉模型失败时返回结构化错误，不中断主模型本轮回答。"""
        llm = RecordingVisionLLM(failure=RuntimeError("视觉服务不可用"))
        tool = build_tool(config=build_config(), llm=llm)

        delivery = await tool.deliver(
            items=[artifact("工具图片 1", b"image")],
            truncated_count=0,
            question="请回答",
            source_name="工具图片",
            turn_state=VisionTurnState(),
        )

        self.assertIsNotNone(delivery.result)
        if delivery.result is None:
            raise AssertionError("应返回结构化失败结果")
        self.assertTrue(delivery.result.is_error)
        self.assertIsNone(delivery.result.description)
        self.assertEqual(delivery.result.errors[0].error_type, "RuntimeError")
        self.assertIn("视觉服务不可用", delivery.working_messages[0].text or "")
        self.assertEqual(delivery.history_messages, [])

    async def test_multimodal_main_model_receives_images_directly(self) -> None:
        """多模态主模型按输入顺序直接获得图片字节。"""
        llm = RecordingVisionLLM()
        tool = build_tool(
            config=build_config(supports_multimodal=True),
            llm=llm,
        )

        delivery = await tool.deliver(
            items=[
                artifact("当前消息第 1 张图片", b"current"),
                artifact("引用消息第 1 张图片", b"quoted"),
            ],
            truncated_count=0,
            question="看图",
            source_name="当前消息和引用消息",
            turn_state=VisionTurnState(),
        )

        self.assertEqual(llm.requests, [])
        self.assertEqual(delivery.working_messages[0].image, [b"current", b"quoted"])
        self.assertEqual(delivery.history_messages, [])
        self.assertIsNotNone(delivery.result)
        if delivery.result is None:
            raise AssertionError("应返回直接传图结果")
        self.assertEqual(delivery.result.observed_count, 2)

    async def test_single_turn_limit_applies_before_later_tool_images(self) -> None:
        """当前与引用图片先占用上限，后续工具图片会明确标记截断。"""
        llm = RecordingVisionLLM()
        tool = build_tool(
            config=build_config(
                supports_multimodal=True,
                image_delivery_max_images=2,
            ),
            llm=llm,
        )
        state = VisionTurnState()

        first = await tool.deliver(
            items=[
                artifact("当前消息第 1 张图片", b"current"),
                artifact("引用消息第 1 张图片", b"quoted"),
            ],
            truncated_count=0,
            question="看图",
            source_name="当前消息和引用消息",
            turn_state=state,
        )
        second = await tool.deliver(
            items=[artifact("工具图片 1", b"tool")],
            truncated_count=0,
            question="看图",
            source_name="工具返回的图片",
            turn_state=state,
        )

        self.assertEqual(first.working_messages[0].image, [b"current", b"quoted"])
        self.assertIsNotNone(second.result)
        if second.result is None:
            raise AssertionError("应返回截断结果")
        self.assertEqual(second.result.observed_count, 0)
        self.assertEqual(second.result.truncated_count, 1)
        self.assertIn("未观察图片数：1", second.working_messages[0].text or "")

    async def test_limit_preserves_failure_and_success_order(self) -> None:
        """读取失败也占用原位置，不能让后面的成功图片越过单轮上限。"""
        llm = RecordingVisionLLM()
        tool = build_tool(
            config=build_config(
                supports_multimodal=True,
                image_delivery_max_images=1,
            ),
            llm=llm,
        )

        delivery = await tool.deliver(
            items=[
                LLMImageError(
                    label="当前消息第 1 张图片",
                    error_type="ReadTimeout",
                    error="下载超时",
                ),
                artifact("当前消息第 2 张图片", b"second-image"),
            ],
            truncated_count=0,
            question="看图",
            source_name="当前消息",
            turn_state=VisionTurnState(),
        )

        self.assertIsNotNone(delivery.result)
        if delivery.result is None:
            raise AssertionError("应返回失败与截断结果")
        self.assertEqual(delivery.result.observed_count, 0)
        self.assertEqual(delivery.result.truncated_count, 1)
        self.assertEqual(delivery.result.errors[0].label, "当前消息第 1 张图片")
        self.assertIsNone(delivery.working_messages[0].image)

    async def test_same_image_content_is_deduplicated_even_if_label_changes(self) -> None:
        """同一问题下相同图片内容只请求一次视觉模型。"""
        llm = RecordingVisionLLM()
        tool = build_tool(config=build_config(), llm=llm)
        state = VisionTurnState()

        first = await tool.deliver(
            items=[artifact("工具 A 图片 1", b"same-image")],
            truncated_count=0,
            question="这是什么？",
            source_name="工具 A",
            turn_state=state,
        )
        second = await tool.deliver(
            items=[artifact("工具 B 图片 1", b"same-image")],
            truncated_count=0,
            question="这是什么？",
            source_name="工具 B",
            turn_state=state,
        )

        self.assertEqual(len(llm.requests), 1)
        self.assertEqual(len(first.working_messages), 1)
        self.assertEqual(second.working_messages, [])
        self.assertEqual(second.history_messages, [])

    async def test_overlapping_image_batches_only_send_new_content(self) -> None:
        """后续批次与前一批部分重叠时，只把新图片交给视觉模型。"""
        llm = RecordingVisionLLM()
        tool = build_tool(config=build_config(), llm=llm)
        state = VisionTurnState()

        _ = await tool.deliver(
            items=[
                artifact("第一张", b"image-a"),
                artifact("第二张", b"image-b"),
            ],
            truncated_count=0,
            question="这是什么？",
            source_name="第一批",
            turn_state=state,
        )
        _ = await tool.deliver(
            items=[
                artifact("重复的第二张", b"image-b"),
                artifact("第三张", b"image-c"),
            ],
            truncated_count=0,
            question="这是什么？",
            source_name="第二批",
            turn_state=state,
        )

        self.assertEqual(len(llm.requests), 2)
        self.assertEqual(llm.requests[0][1].image, [b"image-a", b"image-b"])
        self.assertEqual(llm.requests[1][1].image, [b"image-c"])
        self.assertEqual(state.consumed_image_slots, 3)

    async def test_description_persistence_can_be_disabled(self) -> None:
        """关闭配置后仍向主模型提供描述，但不写入长期上下文。"""
        llm = RecordingVisionLLM()
        tool = build_tool(
            config=build_config(persist_vision_descriptions=False),
            llm=llm,
        )

        delivery = await tool.deliver(
            items=[artifact("当前消息第 1 张图片", b"image")],
            truncated_count=0,
            question="看图",
            source_name="当前消息",
            turn_state=VisionTurnState(),
        )

        self.assertEqual(len(delivery.working_messages), 1)
        self.assertEqual(delivery.history_messages, [])


if __name__ == "__main__":
    unittest.main()
