"""NapCat API 响应模型。"""

from app.models.common import JsonValue, NapCatModel


class Response(NapCatModel):
    """WebSocket Action 响应。"""

    status: str
    retcode: int
    data: JsonValue = None
    message: str = ""
    echo: str | None = None
    wording: str = ""
