from pydantic import BaseModel
from typing import  Literal


class MessageData(BaseModel):
    message_id: int


class StreamData(BaseModel):
    type: Literal["stream", "response"]
    data_type: Literal["data_chunk", "data_complete"]
    data: str | Literal["Stream transmission complete"]


class Response(BaseModel):
    status: str
    retcode: int
    data: MessageData | StreamData | dict
    message: str
    echo: str | None = None
    wording: str
    stream: Literal["stream-action", "normal-action"] | None = None
