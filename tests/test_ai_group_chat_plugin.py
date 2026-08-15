"""AI 群聊插件端到端假 Bot / 假 LLM 烟测。"""

import unittest
from typing import cast

import httpx

from app.database import GroupDataScope, GroupMessageReader, StoredGroupMessage
from app.models import (
    At,
    GroupMessage,
    Image,
    JsonObject,
    MessageSegment,
    Response,
    Sender,
    Text,
)
from app.plugins.ai_group_chat.ai_group_chat import AIGroupChatPlugin
from app.plugins.ai_group_chat.config import AIGroupChatConfig
from app.plugins.ai_group_chat.debug_dump import AIGroupChatDebugDumper
from app.plugins.ai_group_chat.message_builder import GroupChatMessageBuilder
from app.plugins.ai_group_chat.tool_loop import GroupChatToolLoop
from app.plugins.ai_group_chat.vision_tool import VisionDescriptionTool
from app.plugins.base import Context
from app.services import ChatMessage, ContextHandler
from app.services.llm.schemas import (
    LLMResponse,
    LLMToolChoice,
    LLMToolDefinition,
)

VISION_SYSTEM_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/system.md"
VISION_USER_PROMPT_PATH = "tests/fixtures/ai_group_chat/vision/user.md"


class SmokeBot:
    """提供图片刷新和群消息发送能力的假 Bot。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.boot_id = "10000"
        self.image_calls: list[tuple[str | None, str | None]] = []
        self.sent_texts: list[str] = []

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """返回固定图片字节。"""
        self.image_calls.append((file_id, file))
        return Response(
            status="ok",
            retcode=0,
            data={"base64": "c21va2UtaW1hZ2U="},
        )

    async def send_msg(
        self,
        *,
        group_id: str,
        text: str | None = None,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """记录发送给群内的最终 content。"""
        _ = group_id
        if text is not None:
            self.sent_texts.append(text)
        if message_segment is not None:
            self.sent_texts.append(
                "".join(
                    segment.data.text
                    for segment in message_segment
                    if isinstance(segment, Text)
                )
            )
        return Response(status="ok", retcode=0)

    async def send_group_forward_msg(
        self, *, group_id: str, messages: list[MessageSegment]
    ) -> Response:
        """本烟测不应发送合并转发。"""
        _ = (group_id, messages)
        raise AssertionError("短回复不应使用合并转发")


class SmokeDatabase:
    """引用消息查询始终为空。"""

    async def get_active(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
    ) -> StoredGroupMessage | None:
        """返回空引用上下文。"""
        _ = (scope, message_id)
        return None


class EmptyToolManager:
    """不暴露 MCP 工具。"""

    def list_tools(self) -> list[LLMToolDefinition]:
        """返回空定义。"""
        return []

    async def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        """不存在可调用工具。"""
        _ = (name, arguments)
        raise KeyError(name)


class SmokeLLM:
    """视觉请求返回描述，正式请求返回最终 content。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.vision_models: list[tuple[str, str]] = []
        self.formal_models: list[tuple[str, str]] = []
        self.formal_messages: list[list[ChatMessage]] = []

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        retry_count: int | None = None,
        retry_delay: float | None = None,
    ) -> str:
        """检查独立视觉请求只包含两条消息。"""
        _ = (retry_count, retry_delay)
        self.vision_models.append((model_vendors, model_name))
        if [message.role for message in messages] != ["system", "user"]:
            raise AssertionError("视觉请求不应携带群聊历史")
        return "图片中写着“测试成功”。"

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        tools: list[LLMToolDefinition],
        tool_choice: LLMToolChoice = "auto",
        parallel_tool_calls: bool = True,
    ) -> LLMResponse:
        """记录正式主模型请求。"""
        _ = (tools, tool_choice, parallel_tool_calls)
        self.formal_models.append((model_vendors, model_name))
        self.formal_messages.append(messages[:])
        return LLMResponse(
            content="图片里写着测试成功。",
            reasoning_content="这段内容不能发到群里",
        )


class SmokeContext:
    """组合烟测依赖。"""

    def __init__(self) -> None:
        """初始化假 Bot、数据库、LLM 和工具管理器。"""
        self.bot = SmokeBot()
        self.group_messages = SmokeDatabase()
        self.direct_httpx = cast(httpx.AsyncClient, object())
        self.llm = SmokeLLM()
        self.mcp_tool_manager = EmptyToolManager()


def build_config() -> AIGroupChatConfig:
    """构造文本主模型与独立视觉模型配置。"""
    return AIGroupChatConfig(
        model_name="main-model",
        model_vendors="main-vendor",
        supports_multimodal=False,
        vision_model_name="vision-model",
        vision_model_vendors="vision-vendor",
        vision_system_prompt_path=VISION_SYSTEM_PROMPT_PATH,
        vision_user_prompt_path=VISION_USER_PROMPT_PATH,
        output_reasoning_content=False,
        persist_vision_descriptions=True,
        group_config=[],
    )


def build_event() -> GroupMessage:
    """构造艾特机器人并附图的群消息。"""
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id="30000",
        group_id="40000",
        group_name="测试群",
        message=[
            At.new("10000"),
            Text.new("请看图回答"),
            Image.new("smoke.png"),
        ],
        raw_message="[CQ:at,qq=10000]请看图回答[图片]",
        sender=Sender(user_id="20000", nickname="测试用户", role="member"),
    )


class AIGroupChatPluginSmokeTest(unittest.IsolatedAsyncioTestCase):
    """验证附图消息从读取到最终群回复的完整编排。"""

    async def test_non_multimodal_main_model_uses_internal_vision_tool(self) -> None:
        """独立视觉模型描述图片，正式回复始终由主模型生成。"""
        smoke_context = SmokeContext()
        context = cast(Context, smoke_context)
        config = build_config()
        plugin = object.__new__(AIGroupChatPlugin)
        plugin.context = context
        plugin.config = config
        plugin.group_contexts = {
            "40000": ContextHandler(
                system_prompt="角色、知识库和通用群聊要求",
                max_context_tokens=1000000,
            )
        }
        plugin.debug_dumper = AIGroupChatDebugDumper(config=config)
        plugin.message_builder = GroupChatMessageBuilder(
            config=config,
            group_messages=cast(GroupMessageReader, smoke_context.group_messages),
            bot=smoke_context.bot,
            http_client=smoke_context.direct_httpx,
        )
        plugin.vision_tool = VisionDescriptionTool(
            config=config,
            context=context,
        )
        plugin.tool_loop = GroupChatToolLoop(
            config=config,
            context=context,
            debug_dumper=plugin.debug_dumper,
            vision_tool=plugin.vision_tool,
        )

        handled = await plugin.run(build_event())

        self.assertTrue(handled)
        self.assertEqual(smoke_context.bot.image_calls, [(None, "smoke.png")])
        self.assertEqual(
            smoke_context.llm.vision_models,
            [("vision-vendor", "vision-model")],
        )
        self.assertEqual(
            smoke_context.llm.formal_models,
            [("main-vendor", "main-model")],
        )
        request_text = "\n".join(
            message.text or ""
            for message in smoke_context.llm.formal_messages[0]
        )
        self.assertIn("系统生成，不是用户原话", request_text)
        self.assertIn("图片中写着“测试成功”", request_text)
        self.assertEqual(
            smoke_context.bot.sent_texts,
            ["图片里写着测试成功。"],
        )
        stored_messages = plugin.group_contexts["40000"].messages_lst
        self.assertTrue(all(message.image is None for message in stored_messages))
        self.assertIn(
            "图片中写着“测试成功”",
            "\n".join(message.text or "" for message in stored_messages),
        )


if __name__ == "__main__":
    unittest.main()
