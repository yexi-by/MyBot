from pydantic import BaseModel
from typing import Any, Literal


class MessageData(BaseModel):
    message_id: int


class Response(BaseModel):
    status: str
    retcode: int
    data: MessageData | Any
    message: str
    echo: str | None = None
    wording: str
    stream: Literal["stream-action", "normal-action"]|None=None
