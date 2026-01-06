from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """
    所有 AI 服务商必须继承的基类
    """

    @abstractmethod
    async def get_ai_response(
        self,
        messages: list,
        model: str,
        **kwargs,
    ) -> str:
        pass

    async def get_image(
        self,
        prompt: str,
        model: str,
        image_base64_list: list[str] | None = None,
    ) -> str:
        """
        生成图片（可选方法，不是所有提供商都支持）

        Args:
            prompt: 文本提示词
            model: 模型名称
            image_base64_list: 可选的图片列表(base64编码的字符串)，用于图文生图

        Returns:
            生成的图片base64编码字符串
        """
        raise NotImplementedError(f"{self.__class__.__name__} 不支持图像生成功能")
