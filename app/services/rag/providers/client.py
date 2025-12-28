from ..schemas import EmbeddingConfig
import httpx
from app.utils import create_retry_manager


class SiliconFlowEmbedding:
    def __init__(
        self, embedding_config: EmbeddingConfig, client: httpx.AsyncClient
    ) -> None:
        self.embedding_config = embedding_config
        self.client = client

    async def _send_request(
        self,
        model: str,
        text: str | list[str],
        **kwargs,
    ) -> dict:
        payload = {"model": model, "input": text}
        headers = {
            "Authorization": f"Bearer {self.embedding_config.api_key}",
            "Content-Type": "application/json",
        }
        response = await self.client.post(
            self.embedding_config.base_url, json=payload, headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def get_embedding(
        self,
        model: str,
        text: str | list[str],
        **kwargs,
    ) -> dict:
        retrier = create_retry_manager(
            retry_count=self.embedding_config.retry_count,
            retry_delay=self.embedding_config.retry_delay,
            error_types=(
                httpx.HTTPStatusError,
                httpx.RequestError,
            ),
        )
        async for attempt in retrier:
            with attempt:
                response = await self._send_request(model=model, text=text, **kwargs)
                return response
        raise RuntimeError("Retries exhausted")  # 规避下类型检查,这行是死代码
