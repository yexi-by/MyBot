"""AI 群聊 Markdown 调试转储测试。"""

import tempfile
import unittest
from pathlib import Path

from app.plugins.ai_group_chat.config import AIGroupChatConfig, GroupChatConfig
from app.plugins.ai_group_chat.debug_dump import AIGroupChatDebugDumper
from app.services import ChatMessage
from app.services.llm.schemas import LLMResponse, LLMToolCall, LLMToolDefinition


def build_config(*, enabled: bool) -> AIGroupChatConfig:
    """构造测试用 AI 群聊配置。"""
    return AIGroupChatConfig(
        model_name="gpt-5.5",
        model_vendors="CLIProxyAPI",
        debug_dump_messages=enabled,
        group_config=[],
    )


def build_group_config() -> GroupChatConfig:
    """构造测试用群配置。"""
    return GroupChatConfig(
        group_id="40000",
        system_prompt_path="prompts/system.md",
        knowledge_base_path="prompts/knowledge.md",
        max_context_length=10,
    )


class AIGroupChatDebugDumperTest(unittest.IsolatedAsyncioTestCase):
    """验证 AI 群聊 Markdown 调试文件内容。"""

    async def test_enabled_dumper_writes_startup_and_llm_sections(self) -> None:
        """开启开关后，调试文件会记录启动 messages、请求和响应。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            dumper = AIGroupChatDebugDumper(config=build_config(enabled=True))
            dumper.root_dir = Path(temp_dir)
            group_config = build_group_config()
            path = dumper.initialize_group(
                group_config=group_config,
                messages=[ChatMessage(role="system", text="系统提示词")],
            )
            self.assertIsNotNone(path)
            if path is None:
                raise AssertionError("调试文件路径不应为空")

            await dumper.append_llm_request(
                group_id="40000",
                round_index=1,
                messages=[
                    ChatMessage(role="system", text="系统提示词"),
                    ChatMessage(role="user", text="用户消息"),
                ],
                tools=[
                    LLMToolDefinition(
                        name="test__lookup",
                        description="查询测试信息。",
                        parameters={
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False,
                        },
                    )
                ],
                tool_choice="auto",
            )
            await dumper.append_llm_response(
                group_id="40000",
                round_index=1,
                response=LLMResponse(
                    content="正式回复",
                    reasoning_content="模型思考",
                    tool_calls=[
                        LLMToolCall(
                            id="call-1",
                            name="test__lookup",
                            arguments={},
                        )
                    ],
                ),
                modifier_calls=[],
                information_calls=[
                    LLMToolCall(id="call-1", name="test__lookup", arguments={})
                ],
            )

            content = path.read_text(encoding="utf-8")
            self.assertIn("启动初始化 messages", content)
            self.assertIn("发送给模型的 messages", content)
            self.assertIn("系统提示词", content)
            self.assertIn("用户消息", content)
            self.assertIn("test__lookup", content)
            self.assertIn("模型思考", content)

    async def test_disabled_dumper_does_not_create_files(self) -> None:
        """关闭开关后，调试转储不会创建任何文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            dumper = AIGroupChatDebugDumper(config=build_config(enabled=False))
            dumper.root_dir = Path(temp_dir)

            path = dumper.initialize_group(
                group_config=build_group_config(),
                messages=[ChatMessage(role="system", text="系统提示词")],
            )

            self.assertIsNone(path)
            self.assertEqual(list(Path(temp_dir).iterdir()), [])
