from pydantic import TypeAdapter, ValidationError
from app.models import AllEvent

class EventTypeChecker:
    """类型判断"""

    def __init__(self) -> None:
        self.adapter = TypeAdapter(AllEvent)

    def get_event(self, data: dict) -> AllEvent | None:
        try:
            return self.adapter.validate_python(data)
        except ValidationError:
            return None