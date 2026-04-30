"""LLM 工具注册表测试。"""

import unittest
from typing import cast

from pydantic import Field

from app.models import JsonObject, JsonValue, StrictModel
from app.services.llm.tools import LLMToolRegistry, tool_result_to_text


class DemoToolArgs(StrictModel):
    """测试用工具参数。"""

    required_text: str
    optional_count: int = Field(default=3)


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
