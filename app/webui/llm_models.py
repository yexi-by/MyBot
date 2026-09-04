"""WebUI 模型列表代理：按配置文件中的 provider 参数拉取 OpenAI 兼容 /models。"""

import time
from pathlib import Path

from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from app.services.llm.errors import provider_error_message

from .config_io import read_config_payload


class ProviderNotFoundError(LookupError):
    """指定 provider 未在配置文件中定义。"""


class ProviderModelsError(RuntimeError):
    """模型列表拉取失败，message 已脱敏可直接展示。"""


_CACHE_TTL_SECONDS = 30.0
_models_cache: dict[str, tuple[float, list[str]]] = {}


def clear_models_cache() -> None:
    """清空模型列表缓存；供测试隔离与运维排障使用。"""
    _models_cache.clear()


async def list_provider_models(*, config_file: Path, provider_id: str) -> list[str]:
    """读取配置文件中的 provider 连接参数，代理拉取其模型 id 列表。

    provider 未定义抛出 ProviderNotFoundError；配置无效或上游不可达抛出
    ProviderModelsError。结果按（配置哈希, provider）缓存 30 秒，
    配置一旦变化（哈希改变）自动失效。
    """
    result = read_config_payload(config_file=config_file)
    if result.parsed is None:
        raise ProviderModelsError("配置当前无效，请先修复配置错误")
    provider_config = result.parsed.llm.providers.get(provider_id)
    if provider_config is None:
        raise ProviderNotFoundError(provider_id)

    cache_key = f"{result.sha256}:{provider_id}"
    cached = _models_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    network = result.parsed.network
    api_key = (
        provider_config.api_key.get_secret_value()
        if provider_config.api_key is not None
        else ""
    )
    proxy = (
        provider_config.proxy
        if provider_config.proxy is not None
        else network.proxy if provider_config.inherit_network_proxy else None
    )
    timeout = (
        network.timeout_seconds
        if provider_config.timeout_seconds == 0
        else provider_config.timeout_seconds
    )
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=provider_config.base_url,
        timeout=timeout,
        max_retries=0,
        http_client=(
            DefaultAsyncHttpxClient(proxy=proxy, timeout=timeout)
            if proxy is not None
            else None
        ),
    )
    try:
        page = await client.models.list()
        models = sorted({model.id for model in page.data})
    except Exception as exc:
        raise ProviderModelsError(f"拉取模型列表失败：{provider_error_message(exc)}") from None
    finally:
        await client.close()
    if not models:
        raise ProviderModelsError("上游返回了空的模型列表")
    _models_cache[cache_key] = (time.monotonic(), models)
    return models
