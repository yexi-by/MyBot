"""NapCat WebSocket Action 请求模型。"""

from app.models.common import JsonObject, NapCatModel


class ActionPayload(NapCatModel):
    """NapCat WebSocket 请求载荷。"""

    action: str
    params: JsonObject | None = None
    echo: str | None = None
