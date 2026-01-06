from google import genai
from google.genai import types
from openai import AsyncOpenAI
from typing import Self
from volcenginesdkarkruntime import AsyncArk
from .schemas import LLMConfig, ChatMessage, LLMProviderWrapper
from .providers.openai import OpenAIService
from .providers.gemini import GeminiService
from .providers.volcengine import VolcengineService
from .wrapper import ResilientLLMProvider


class LLMHandler:
    def __init__(self, services: list[LLMProviderWrapper]) -> None:
        self.services = services

    @classmethod
    def register_instance(cls, settings: list[LLMConfig]) -> Self:
        """注册实例"""
        services = []
        for model_config in settings:
            provider_type = model_config.provider_type
            api_key = model_config.api_key
            base_url = model_config.base_url
            match provider_type:
                case "openai":
                    raw_service = OpenAIService(
                        client=AsyncOpenAI(api_key=api_key, base_url=base_url)
                    )
                case "gemini":
                    raw_service = GeminiService(
                        client=genai.Client(
                            api_key=api_key,
                            http_options=types.HttpOptions(base_url=base_url)
                            if base_url
                            else None,
                        )
                    )
                case "volcengine":
                    raw_service = VolcengineService(
                        client=AsyncArk(api_key=api_key, base_url=base_url)
                    )
                case _:
                    raise ValueError(
                        f"未知的模型服务类型: {model_config.provider_type}"
                    )
            safe_service = ResilientLLMProvider(
                inner_provider=raw_service, llm_config=model_config
            )
            wrapper = LLMProviderWrapper(
                model_vendors=model_config.model_vendors,
                provider=safe_service,
            )
            services.append(wrapper)
        return cls(services=services)

    async def get_ai_text_response(
        self,
        messages: list[ChatMessage],
        model_vendors: str,
        model_name: str,
        **kwargs,
    ) -> str:
        for llm in self.services:
            if llm.model_vendors != model_vendors:
                continue
            return await llm.provider.get_ai_response(
                messages=messages, model=model_name
            )
        raise ValueError(f"未定义的服务商名:{model_vendors}")

    async def get_image(
        self,
        prompt: str,
        model: str,
        model_vendors: str,
        image_base64_list: list[str] | None = None,
    ) -> str:
        """
        生成图片
        
        Args:
            prompt: 文本提示词
            model: 模型名称
            model_vendors: 服务商名称
            image_base64_list: 可选的图片列表(base64编码的字符串)，用于图文生图
            
        Returns:
            生成的图片base64编码字符串
        """
        for llm in self.services:
            if llm.model_vendors != model_vendors:
                continue
            return await llm.provider.get_image(
                prompt=prompt,
                model=model,
                image_base64_list=image_base64_list,
            )
        raise ValueError(f"未定义的服务商名:{model_vendors}")
