"""模型请求的用户可见错误，只描述错误类型和 HTTP 状态。"""

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError


class LLMRequestError(RuntimeError):
    """模型请求最终失败；消息可用于群聊反馈和诊断日志。"""


def provider_error_message(error: Exception) -> str:
    """省略供应商响应正文、请求 URL 和密钥，返回可执行的失败说明。"""
    if isinstance(error, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
        return "模型服务请求超时，请稍后重试。"
    if isinstance(error, APIStatusError):
        status = error.status_code
        if status in {401, 403}:
            return f"模型服务鉴权失败（HTTP {status}），请检查 Provider 密钥与权限。"
        if status == 429:
            return "模型服务限流（HTTP 429），请稍后重试。"
        return f"模型服务请求失败（HTTP {status}），请检查服务状态与模型配置。"
    if isinstance(error, (APIConnectionError, httpx.RequestError, ConnectionError)):
        return "无法连接模型服务，请检查网络与 Provider 地址。"
    return "模型服务未返回可用结果，请检查模型配置或稍后重试。"
