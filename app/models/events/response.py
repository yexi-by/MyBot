from pydantic import BaseModel
from typing import Any


class MessageData(BaseModel):
    message_id: int


class Response(BaseModel):
    status: str
    retcode: int
    data: MessageData | Any
    message: str
    echo: str | None = None
    wording: str
