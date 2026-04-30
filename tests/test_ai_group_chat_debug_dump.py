"""AI 群聊长期上下文 Markdown 调试转储测试。"""

import tempfile
import unittest
from shutil import rmtree
from pathlib import Path

from app.plugins.ai_group_chat.config import AIGroupChatConfig, GroupChatConfig
from app.plugins.ai_group_chat.debug_dump import AIGroupChatDebugDumper
from app.services import ChatMessage
from app.services.llm.schemas import LLMToolCall


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
        max_context_tokens=1000000,
    )


class AIGroupChatDebugDumperTest(unittest.IsolatedAsyncioTestCase):
    """验证 AI 群聊 Markdown 调试文件内容。"""

    async def test_enabled_dumper_only_appends_long_term_context_delta(
        self,
    ) -> None:
        """开启开关后，调试文件只追加长期上下文新增 messages。"""
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

            await dumper.append_context_snapshot(
                group_id="40000",
                title="第一次长期上下文",
                messages=[
                    ChatMessage(role="system", text="系统提示词"),
                    ChatMessage(role="user", text="用户消息"),
                ],
            )
            await dumper.append_context_snapshot(
                group_id="40000",
                title="第二次长期上下文",
                messages=[
                    ChatMessage(role="system", text="系统提示词"),
                    ChatMessage(role="user", text="用户消息"),
                    ChatMessage(role="assistant", text="正式回复"),
                ],
            )

            content = path.read_text(encoding="utf-8")
            self.assertIn("启动初始化长期上下文", content)
            self.assertIn("长期上下文增量 #1", content)
            self.assertIn("长期上下文增量 #2", content)
            self.assertNotIn("模型请求", content)
            self.assertNotIn("模型响应", content)
            self.assertEqual(content.count("#### message 1 / system"), 1)
            self.assertEqual(content.count("用户消息"), 1)
            self.assertEqual(content.count("正式回复"), 1)

    async def test_tool_calls_are_summarized_without_arguments_or_results(
        self,
    ) -> None:
        """工具调用只记录名称摘要，不落真实参数和工具结果正文。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            dumper = AIGroupChatDebugDumper(config=build_config(enabled=True))
            dumper.root_dir = Path(temp_dir)
            path = dumper.initialize_group(
                group_config=build_group_config(),
                messages=[ChatMessage(role="system", text="系统提示词")],
            )
            self.assertIsNotNone(path)
            if path is None:
                raise AssertionError("调试文件路径不应为空")

            await dumper.append_context_snapshot(
                group_id="40000",
                title="包含工具调用的长期上下文",
                messages=[
                    ChatMessage(role="system", text="系统提示词"),
                    ChatMessage(role="user", text="查一下"),
                    ChatMessage(
                        role="assistant",
                        text="我先查一下",
                        tool_calls=[
                            LLMToolCall(
                                id="call-1",
                                name="test__lookup",
                                arguments={"query": "绝密参数"},
                            )
                        ],
                    ),
                    ChatMessage(
                        role="tool",
                        tool_call_id="call-1",
                        text="巨大工具结果正文",
                    ),
                ],
            )

            content = path.read_text(encoding="utf-8")
            self.assertIn("##### 工具调用摘要", content)
            self.assertIn("test__lookup", content)
            self.assertIn("工具参数和工具结果已省略", content)
            self.assertIn("tool message 内容已省略", content)
            self.assertNotIn("绝密参数", content)
            self.assertNotIn("巨大工具结果正文", content)

    async def test_context_reset_writes_rebuilt_context_once(self) -> None:
        """检测到上下文重建时，调试文件标记 context_reset 并写新基线。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            dumper = AIGroupChatDebugDumper(config=build_config(enabled=True))
            dumper.root_dir = Path(temp_dir)
            path = dumper.initialize_group(
                group_config=build_group_config(),
                messages=[ChatMessage(role="system", text="系统提示词")],
            )
            self.assertIsNotNone(path)
            if path is None:
                raise AssertionError("调试文件路径不应为空")

            await dumper.append_context_snapshot(
                group_id="40000",
                title="压缩前长期上下文",
                messages=[
                    ChatMessage(role="system", text="系统提示词"),
                    ChatMessage(role="user", text="历史消息"),
                    ChatMessage(role="assistant", text="历史回复"),
                ],
            )
            await dumper.append_context_snapshot(
                group_id="40000",
                title="压缩后长期上下文",
                messages=[
                    ChatMessage(role="system", text="系统提示词"),
                    ChatMessage(role="user", text="历史摘要 + 当前消息"),
                ],
            )

            content = path.read_text(encoding="utf-8")
            self.assertIn("长期上下文重建 #2", content)
            self.assertIn("context_reset: `True`", content)
            self.assertIn("历史摘要 + 当前消息", content)
            self.assertEqual(content.count("历史消息"), 1)

    async def test_runtime_deleted_debug_directory_is_recreated(self) -> None:
        """运行中删除调试目录后，下一次写入会自动重建而不打断流程。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            dumper = AIGroupChatDebugDumper(config=build_config(enabled=True))
            dumper.root_dir = Path(temp_dir)
            path = dumper.initialize_group(
                group_config=build_group_config(),
                messages=[ChatMessage(role="system", text="系统提示词")],
            )
            self.assertIsNotNone(path)
            if path is None:
                raise AssertionError("调试文件路径不应为空")
            rmtree(path.parent)

            await dumper.append_context_snapshot(
                group_id="40000",
                title="目录删除后的长期上下文",
                messages=[
                    ChatMessage(role="system", text="系统提示词"),
                    ChatMessage(role="user", text="目录删除后新消息"),
                ],
            )

            content = path.read_text(encoding="utf-8")
            self.assertIn("原调试文件或目录曾在运行中被删除", content)
            self.assertIn("目录删除后新消息", content)

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
