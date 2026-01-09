import asyncio
import base64
import io
import random
import zipfile

import httpx

from app.utils import create_retry_manager

from .payload import CharCaption, get_payload
from .utils import reencode_image


class NaiClient:
    def __init__(self, client: httpx.AsyncClient, url: str, api_key: str) -> None:
        self.client = client
        self.url = url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        seed: int | None = None,
        image_base64: str | None = None,
        v4_prompt_char_captions: list[CharCaption] | list[dict] | None = None,
    ) -> str:
        if seed is None:
            seed = random.randint(1, 2**32 - 1)
        new_image_base64 = None
        if image_base64 is not None:
            new_image_base64 = await asyncio.to_thread(reencode_image, image_base64)
        char_caption: list[CharCaption] | list[dict] | None = None
        if v4_prompt_char_captions:
            char_caption = []
            for item in v4_prompt_char_captions:
                if isinstance(item, dict):
                    char_caption.append(CharCaption.model_validate(item))
                else:
                    char_caption.append(item)

        payloads = get_payload(
            prompt=prompt,
            new_negative_prompt=negative_prompt,
            width=width,
            height=height,
            seed=seed,
            v4_prompt_char_captions=char_caption,
            image_base64=new_image_base64,
        )
        retrier = create_retry_manager(
            retry_count=3,
            retry_delay=2,
            error_types=(httpx.HTTPStatusError, httpx.RequestError),
            custom_checker=lambda x: not x,
        )

        async for attempt in retrier:
            with attempt:
                response = await self.client.post(
                    self.url, headers=self.headers, json=payloads.model_dump()
                )
                response.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
                    image_filename = zip_ref.namelist()[0]
                    with zip_ref.open(image_filename) as image_file:
                        image_bytes = image_file.read()
                        base64_string = base64.b64encode(image_bytes).decode("utf-8")
                        return base64_string

        raise RuntimeError("Retries exhausted")  # 规避下类型检查,这行是死代码
