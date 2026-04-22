from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import ChatMessage

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
        message: "ChatMessage",
        model: str,
        **kwargs,
    ) -> str:
        """
        生成图片（可选方法，不是所有提供商都支持）

        Args:
            message: 图像生成消息，至少需要提供文本提示词
            model: 模型名称
            **kwargs: 提供商对应的额外图像生成参数

        Returns:
            生成的图片base64编码字符串
        """
        raise NotImplementedError(f"{self.__class__.__name__} 不支持图像生成功能")
