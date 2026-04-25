"""NapCat API 响应模型。"""

from typing import Literal

from app.models.common import JsonValue, StrictModel


class Response(StrictModel):
    """WebSocket Action 响应。"""

    status: str
    retcode: int
    data: JsonValue = None
    message: str = ""
    echo: str | None = None
    wording: str = ""
    stream: Literal["stream-action", "normal-action"] | None = None
