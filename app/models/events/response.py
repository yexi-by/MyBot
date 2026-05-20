"""NapCat API 响应模型。"""

from typing import Literal

from app.models.common import JsonValue, NapCatModel


class Response(NapCatModel):
    """WebSocket Action 响应。"""

    status: str
    retcode: int
    data: JsonValue = None
    message: str = ""
    echo: str | None = None
    wording: str = ""
    stream: Literal["stream-action", "normal-action"] | None = None


class StreamTransferResult(NapCatModel):
    """NapCat Stream Action 的聚合响应。"""

    packets: list[Response]
    final_response: Response
