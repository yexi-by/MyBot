"""NapCat WebSocket Action 请求模型。"""

from app.models.common import JsonObject, StrictModel


class ActionPayload(StrictModel):
    """NapCat WebSocket 请求载荷。"""

    action: str
    params: JsonObject | None = None
    echo: str | None = None
