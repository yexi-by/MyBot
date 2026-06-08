"""LLM 工具注册表测试。"""

import unittest
from typing import cast

from pydantic import Field

from app.models import JsonObject, JsonValue, StrictModel
from app.services.llm.tools import (
    LLMToolExecutionResult,
    LLMToolImageArtifact,
    LLMToolRegistry,
    tool_result_to_text,
)


class DemoToolArgs(StrictModel):
    """测试用工具参数。"""

    required_text: str
    optional_count: int = Field(default=3)


class EmptyToolArgs(StrictModel):
    """无参数测试工具。"""


class LLMToolRegistryTest(unittest.IsolatedAsyncioTestCase):
    """验证本地工具注册和 strict schema 归一化。"""

    async def test_register_tool_generates_openai_strict_schema(self) -> None:
        """默认值字段也会进入 required，并移除 default。"""
        registry = LLMToolRegistry()

        async def handler(arguments: JsonObject) -> JsonValue:
            """回显工具参数。"""
            return arguments

        registry.register_tool(
            name="demo_tool",
            description="测试工具",
            parameters_model=DemoToolArgs,
            handler=handler,
        )

        tool = registry.list_tools()[0]
        self.assertTrue(tool.strict)
        required = cast(list[str], tool.parameters["required"])
        self.assertEqual(set(required), {"required_text", "optional_count"})
        self.assertIs(tool.parameters["additionalProperties"], False)
        properties = cast(JsonObject, tool.parameters["properties"])
        optional_schema = cast(JsonObject, properties["optional_count"])
        self.assertNotIn("default", optional_schema)

    def test_tool_result_to_text_keeps_string_result(self) -> None:
        """字符串工具结果直接作为 tool message 文本。"""
        self.assertEqual(tool_result_to_text("ok"), "ok")

    async def test_call_tool_with_artifacts_wraps_plain_result(self) -> None:
        """普通工具通过 artifact 接口调用时会自动包成无附件结果。"""
        registry = LLMToolRegistry()

        async def handler(arguments: JsonObject) -> JsonValue:
            """返回普通 JSON 工具结果。"""
            _ = arguments
            return {"ok": True}

        registry.register_tool(
            name="demo_plain_tool",
            description="普通测试工具。",
            parameters_model=EmptyToolArgs,
            handler=handler,
        )

        result = await registry.call_tool_with_artifacts(
            name="demo_plain_tool",
            arguments={},
        )

        self.assertEqual(result.result, {"ok": True})
        self.assertEqual(result.image_artifacts, [])

    async def test_call_tool_with_artifacts_preserves_image_artifacts(self) -> None:
        """artifact-aware 工具可以返回模型可见图片附件。"""
        registry = LLMToolRegistry()

        async def handler(arguments: JsonObject) -> LLMToolExecutionResult:
            """返回带图片附件的工具结果。"""
            _ = arguments
            return LLMToolExecutionResult(
                result={"ok": True, "returned_count": 1},
                image_artifacts=[
                    LLMToolImageArtifact(
                        label="第 1 张图片",
                        image_bytes=b"image-bytes",
                        metadata={"message_index": 1, "image_index": 1},
                    )
                ],
            )

        registry.register_tool(
            name="demo_image_tool",
            description="图片测试工具。",
            parameters_model=EmptyToolArgs,
            handler=handler,
        )

        result = await registry.call_tool_with_artifacts(
            name="demo_image_tool",
            arguments={},
        )

        self.assertEqual(result.result, {"ok": True, "returned_count": 1})
        self.assertEqual(len(result.image_artifacts), 1)
        self.assertEqual(result.image_artifacts[0].image_bytes, b"image-bytes")
