"""事件载荷解析器。"""

from pydantic import TypeAdapter, ValidationError

from app.models import AllEvent
from app.utils.log import log_event


class EventTypeChecker:
    """将 WebSocket 原始数据解析为具体事件模型。"""

    def __init__(self) -> None:
        """初始化事件联合类型适配器。"""
        self.adapter: TypeAdapter[AllEvent] = TypeAdapter(AllEvent)

    def get_event(self, data: dict[str, object]) -> AllEvent | None:
        """解析事件，无法识别时记录原因并返回 None。"""
        try:
            return self.adapter.validate_python(data)
        except ValidationError as exc:
            errors = exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
            first_error = str(errors[0]) if errors else str(exc).splitlines()[0]
            log_event(
                level="WARNING",
                event="websocket.event.parse_failed",
                category="websocket",
                message="NapCat 事件解析失败，已跳过",
                post_type=str(data.get("post_type")),
                message_type=str(data.get("message_type")),
                notice_type=str(data.get("notice_type")),
                key_names=", ".join(sorted(data.keys())),
                error_count=len(errors),
                first_error=first_error,
            )
            return None
