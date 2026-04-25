"""事件载荷解析器。"""

from pydantic import TypeAdapter, ValidationError

from app.models import AllEvent


class EventTypeChecker:
    """将 WebSocket 原始数据解析为具体事件模型。"""

    def __init__(self) -> None:
        """初始化事件联合类型适配器。"""
        self.adapter: TypeAdapter[AllEvent] = TypeAdapter(AllEvent)

    def get_event(self, data: dict[str, object]) -> AllEvent | None:
        """解析事件，无法识别时返回 None。"""
        try:
            return self.adapter.validate_python(data)
        except ValidationError:
            return None
