"""AI 群聊图片直接交付、额度与部分错误测试。"""

import unittest
from io import BytesIO

from PIL import Image as PillowImage

from app.plugins.ai_group_chat.image_delivery import (
    ImageDeliveryError,
    ImageDeliveryService,
    ImageTurnState,
)
from app.services.llm.tools import LLMImageArtifact, LLMImageError
from tests.config_helpers import build_ai_group_chat_config


def build_service(**overrides: object) -> ImageDeliveryService:
    """通过公共配置工厂覆盖当前用例需要的图片限制。"""
    config = build_ai_group_chat_config(overrides={"images": overrides})
    return ImageDeliveryService(config=config.images)


def artifact(label: str, content: bytes) -> LLMImageArtifact:
    """构造来自消息或工具的图片附件。"""
    return LLMImageArtifact(label=label, image_bytes=content)


def image_bytes(*, size: tuple[int, int], image_format: str = "PNG") -> bytes:
    """创建用于真实尺寸和 MIME 检查的小图片。"""
    output = BytesIO()
    with PillowImage.new("RGB", size, "red") as image:
        image.save(output, format=image_format)
    return output.getvalue()


class ImageDeliveryServiceTest(unittest.TestCase):
    """验证附件顺序、原图保留、可恢复错误和跨工具轮次额度。"""

    def test_partial_success_keeps_original_order_and_errors(self) -> None:
        """成功图片直接附带，读取失败与上游截断仍向主模型可见。"""
        error = LLMImageError(label="引用图片 2", error_type="ReadTimeout", error="下载超时")
        delivery = build_service().deliver(
            items=[artifact("当前图片", b"current"), error, artifact("引用图片 1", b"quoted")],
            truncated_count=1,
            source_name="当前消息和引用消息",
            turn_state=ImageTurnState(),
        )

        assert delivery.result is not None
        self.assertTrue(delivery.result.ok)
        self.assertFalse(delivery.result.is_error)
        self.assertEqual(delivery.result.observed_count, 2)
        self.assertEqual(delivery.result.truncated_count, 1)
        self.assertEqual(delivery.result.errors, [error])
        self.assertEqual(delivery.working_messages[0].image, [b"current", b"quoted"])
        self.assertIn(error.error, delivery.working_messages[0].text or "")
        self.assertEqual(delivery.history_messages, [])

    def test_zero_limits_preserve_original_bytes_and_omit_detail(self) -> None:
        """不设限制时不解码重写图片，omit 不向模型交付 detail 参数。"""
        original = b"original-unmodified-image-bytes"
        delivery = build_service(image_detail="omit").deliver(
            items=[artifact("原图", original)],
            truncated_count=0,
            source_name="当前消息",
            turn_state=ImageTurnState(),
        )

        self.assertEqual(delivery.working_messages[0].image, [original])
        self.assertIsNone(delivery.working_messages[0].image_detail)

    def test_size_limit_skips_or_stops_without_consuming_failed_slots(self) -> None:
        """原图超限与读取器报告超限遵守同一策略，失败不占成功图片额度。"""
        oversized_inputs = [
            artifact("超限图", b"1234"),
            LLMImageError(label="超限图", error_type="ImageReadTooLargeError", error="超过配置上限"),
        ]
        for oversized in oversized_inputs:
            with self.subTest(input_type=type(oversized).__name__, policy="skip"):
                state = ImageTurnState()
                delivery = build_service(max_per_turn=1, max_image_bytes=3).deliver(
                    items=[oversized, artifact("正常图", b"ok")],
                    truncated_count=0,
                    source_name="工具附件",
                    turn_state=state,
                )
                assert delivery.result is not None
                self.assertEqual(delivery.working_messages[0].image, [b"ok"])
                self.assertEqual(delivery.result.observed_count, 1)
                self.assertEqual(delivery.result.errors[0].error_type, "ImageReadTooLargeError")
                self.assertEqual(state.consumed_image_slots, 1)
            with self.subTest(input_type=type(oversized).__name__, policy="error"):
                with self.assertRaises(ImageDeliveryError):
                    build_service(max_image_bytes=3, oversize_behavior="error").deliver(
                        items=[oversized], truncated_count=0,
                        source_name="当前消息", turn_state=ImageTurnState(),
                    )

    def test_all_reads_failed_remains_recoverable(self) -> None:
        """没有可用图片时返回读取错误，失败结果不保存为长期图片上下文。"""
        error = LLMImageError(label="当前图片", error_type="NapCatActionFailed", error="图片不存在")
        delivery = build_service(retain_images=True).deliver(
            items=[error], truncated_count=0,
            source_name="当前消息", turn_state=ImageTurnState(),
        )

        assert delivery.result is not None
        self.assertFalse(delivery.result.ok)
        self.assertTrue(delivery.result.is_error)
        self.assertEqual(delivery.result.observed_count, 0)
        self.assertIsNone(delivery.working_messages[0].image)
        self.assertIn(error.error, delivery.working_messages[0].text or "")
        self.assertEqual(delivery.history_messages, [])

    def test_single_turn_limit_includes_later_tool_images(self) -> None:
        """当前和引用图片先占用额度，后续工具图片按同一轮上限截断。"""
        service = build_service(max_per_turn=2)
        state = ImageTurnState()
        first = service.deliver(
            items=[artifact("当前图片", b"current"), artifact("引用图片", b"quoted")],
            truncated_count=0, source_name="消息图片", turn_state=state,
        )
        second = service.deliver(
            items=[artifact("工具图片", b"tool")],
            truncated_count=2, source_name="工具返回图片", turn_state=state,
        )

        self.assertEqual(first.working_messages[0].image, [b"current", b"quoted"])
        assert second.result is not None
        self.assertEqual(second.result.observed_count, 0)
        self.assertEqual(second.result.truncated_count, 3)
        self.assertIsNone(second.working_messages[0].image)
        self.assertEqual(state.consumed_image_slots, 2)

    def test_read_failures_leave_capacity_for_successful_images(self) -> None:
        """读取失败不会耗尽本轮额度，后续成功图片仍交付主模型。"""
        delivery = build_service(max_per_turn=1).deliver(
            items=[
                LLMImageError(label="第一张", error_type="ReadTimeout", error="下载超时"),
                artifact("第二张", b"second"),
            ],
            truncated_count=0, source_name="当前消息", turn_state=ImageTurnState(),
        )

        assert delivery.result is not None
        self.assertEqual(delivery.result.observed_count, 1)
        self.assertEqual(delivery.result.truncated_count, 0)
        self.assertEqual(delivery.result.errors[0].label, "第一张")
        self.assertEqual(delivery.working_messages[0].image, [b"second"])

    def test_overlapping_batches_only_deliver_new_content(self) -> None:
        """同批与跨批重复图片不重复交付，也不重复占用数量或字节额度。"""
        service = build_service(max_per_turn=3, max_total_bytes_per_request=3)
        state = ImageTurnState()
        first = service.deliver(
            items=[artifact("第一张", b"a"), artifact("重复第一张", b"a"), artifact("第二张", b"b")],
            truncated_count=0, source_name="第一批", turn_state=state,
        )
        second = service.deliver(
            items=[artifact("改名的第二张", b"b"), artifact("第三张", b"c")],
            truncated_count=0, source_name="第二批", turn_state=state,
        )
        repeated = service.deliver(
            items=[artifact("再次出现", b"a")],
            truncated_count=0, source_name="第三批", turn_state=state,
        )

        self.assertEqual(first.working_messages[0].image, [b"a", b"b"])
        self.assertEqual(second.working_messages[0].image, [b"c"])
        self.assertEqual(repeated.working_messages, [])
        self.assertEqual(repeated.history_messages, [])
        self.assertIsNone(repeated.result)
        self.assertEqual(state.consumed_image_slots, 3)
        self.assertEqual(state.consumed_image_bytes, 3)

    def test_total_byte_budget_is_shared_and_skips_only_images_that_do_not_fit(self) -> None:
        """前批图片占用总字节；超限图片跳过后，较小图片仍可交付。"""
        service = build_service(max_total_bytes_per_request=8)
        state = ImageTurnState()
        service.deliver(
            items=[artifact("消息图片", b"1234")],
            truncated_count=0, source_name="当前消息", turn_state=state,
        )
        result = service.deliver(
            items=[artifact("工具大图", b"abcdef"), artifact("工具小图", b"xyz")],
            truncated_count=0, source_name="工具附件", turn_state=state,
        )

        assert result.result is not None
        self.assertEqual(result.working_messages[0].image, [b"xyz"])
        self.assertEqual(result.result.errors[0].error_type, "ImageRequestBytesExceeded")
        self.assertEqual(result.result.truncated_count, 1)
        self.assertEqual(state.consumed_image_bytes, 7)
        self.assertEqual(state.consumed_image_slots, 2)
        with self.assertRaises(ImageDeliveryError):
            build_service(max_total_bytes_per_request=8, oversize_behavior="error").deliver(
                items=[artifact("仍然超限", b"zz")], truncated_count=0,
                source_name="工具附件", turn_state=state,
            )
        self.assertEqual(state.consumed_image_bytes, 7)

    def test_byte_rejection_does_not_take_the_last_available_image_slot(self) -> None:
        """数量与总字节同时受限时，前面的失败图片不挤掉后面的合规图片。"""
        for prior_count in (0, 1):
            with self.subTest(prior_count=prior_count):
                service = build_service(
                    max_per_turn=prior_count + 1,
                    max_total_bytes_per_request=prior_count + 3,
                )
                state = ImageTurnState()
                if prior_count:
                    service.deliver(
                        items=[artifact("已交付图片", b"p")], truncated_count=0,
                        source_name="当前消息", turn_state=state,
                    )
                delivery = service.deliver(
                    items=[artifact("超出总字节的大图", b"abcd"), artifact("合规小图", b"x")],
                    truncated_count=0, source_name="工具附件", turn_state=state,
                )

                assert delivery.result is not None
                self.assertEqual(delivery.working_messages[0].image, [b"x"])
                self.assertEqual(delivery.result.observed_count, 1)
                self.assertEqual(delivery.result.truncated_count, 1)
                self.assertEqual(delivery.result.errors[0].error_type, "ImageRequestBytesExceeded")
                self.assertEqual(state.consumed_image_slots, prior_count + 1)
                self.assertEqual(state.consumed_image_bytes, prior_count + 1)

    def test_dimensions_and_mime_limits_keep_valid_original_images(self) -> None:
        """真实尺寸和 MIME 检查只拒绝不符附件，合规原图保持原始字节。"""
        valid = image_bytes(size=(8, 4))
        cases = [
            ({"max_width": 8}, image_bytes(size=(9, 4)), "ImageDimensionsExceeded"),
            ({"max_height": 4}, image_bytes(size=(8, 5)), "ImageDimensionsExceeded"),
            ({"allowed_mime_types": ["image/png"]}, image_bytes(size=(8, 4), image_format="JPEG"), "UnsupportedImageType"),
            ({"allowed_mime_types": ["image/png"]}, b"invalid-image", "ValueError"),
        ]
        for overrides, rejected, error_type in cases:
            with self.subTest(overrides=overrides, error_type=error_type):
                delivery = build_service(**overrides).deliver(
                    items=[artifact("不合规图片", rejected), artifact("合规原图", valid)],
                    truncated_count=0, source_name="工具附件", turn_state=ImageTurnState(),
                )
                assert delivery.result is not None
                self.assertEqual(delivery.working_messages[0].image, [valid])
                self.assertEqual(delivery.result.observed_count, 1)
                self.assertEqual(delivery.result.errors[0].error_type, error_type)

    def test_retention_only_saves_successfully_attached_images(self) -> None:
        """开关只控制长期上下文，本轮始终收到相同原图。"""
        for retain in (False, True):
            with self.subTest(retain_images=retain):
                delivery = build_service(retain_images=retain).deliver(
                    items=[artifact("原图", b"original")],
                    truncated_count=0, source_name="当前消息", turn_state=ImageTurnState(),
                )
                self.assertEqual(delivery.working_messages[0].image, [b"original"])
                self.assertEqual(
                    delivery.history_messages,
                    delivery.working_messages if retain else [],
                )
