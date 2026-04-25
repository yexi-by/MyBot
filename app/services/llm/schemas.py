"""LLM 服务配置与消息模型。"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, field_serializer, model_validator

from app.models import JsonObject, JsonValue, StrictModel

type ImageGenerationSize = Literal[
    "auto",
    "256x256",
    "512x512",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "1792x1024",
    "1024x1792",
]
type ImageGenerationQuality = Literal["standard", "low", "medium", "high", "auto"]
type ImageInputFidelity = Literal["high", "low"]


class ImageGenerationOptions(StrictModel):
    """定义图片生成接口的通用可选参数。"""

    size: ImageGenerationSize | None = None
    quality: ImageGenerationQuality | None = None
    input_fidelity: ImageInputFidelity | None = None


class LLMConfig(StrictModel):
    """定义单个 LLM 服务商配置。"""

    api_key: str
    base_url: str | None = None
    model_vendors: str
    provider_type: Literal["openai"]
    retry_count: int
    retry_delay: int


class ChatMessage(StrictModel):
    """定义传递给 LLM 的统一聊天消息。"""

    role: Literal["system", "user", "assistant", "tool"]
    text: str | None = None
    reasoning_content: str | None = None
    image: list[bytes] | None = None
    tool_calls: list["LLMToolCall"] | None = None
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def check_at_least_one(self) -> "ChatMessage":
        """确保不同角色消息都满足 OpenAI 工具调用协议约束。"""
        if self.role == "tool":
            if self.tool_call_id is None or self.text is None:
                raise ValueError("tool 消息必须提供 tool_call_id 和 text")
            return self
        if self.tool_calls:
            if self.role != "assistant":
                raise ValueError("只有 assistant 消息可以携带 tool_calls")
            return self
        if self.text is None and self.image is None:
            raise ValueError("必须提供 text、image 或 tool_calls")
        return self

    @field_serializer("image")
    def serialize_image(
        self, image: list[bytes] | None, _info: object
    ) -> list[str] | None:
        """序列化图片时只记录长度，避免日志输出二进制内容。"""
        if image is None:
            return None
        return [f"此图片字节码长度为{len(image_bytes)}" for image_bytes in image]


class LLMProviderProtocol(Protocol):
    """描述 LLM 服务实现需要提供的最小接口。"""

    async def get_ai_response(
        self, messages: list[ChatMessage], model: str
    ) -> str:
        """获取文本响应。"""
        ...

    async def get_ai_response_with_tools(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list["LLMToolDefinition"],
        tool_choice: "LLMToolChoice" = "auto",
        parallel_tool_calls: bool = True,
    ) -> "LLMResponse":
        """获取可能包含工具调用的结构化响应。"""
        ...

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
        options: ImageGenerationOptions | None = None,
    ) -> str:
        """获取图片响应。"""
        ...


@dataclass
class LLMProviderWrapper:
    """绑定模型厂商名称与具体服务实现。"""

    model_vendors: str
    provider: LLMProviderProtocol


class LLMContextConfig(StrictModel):
    """定义 LLM 上下文管理配置。"""

    system_prompt_path: str
    max_context_tokens: int


type LLMToolChoice = Literal["auto", "none", "required"] | JsonObject


class LLMToolDefinition(StrictModel):
    """LLM 可调用工具定义。"""

    name: str
    description: str
    parameters: JsonObject
    strict: bool = True


class LLMToolCall(StrictModel):
    """模型请求调用的工具。"""

    id: str
    name: str
    arguments: JsonObject = Field(default_factory=dict)


class LLMResponse(StrictModel):
    """LLM 单轮结构化响应。"""

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)


class LLMToolExecutor(Protocol):
    """工具执行器协议，MCP 与本地工具都实现此接口。"""

    def list_tools(self) -> list[LLMToolDefinition]:
        """返回当前可用工具定义。"""
        ...

    async def call_tool(self, name: str, arguments: JsonObject) -> JsonValue:
        """调用指定工具并返回 JSON 可序列化结果。"""
        ...
