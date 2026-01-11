from typing import Literal

from pydantic import BaseModel

class MessageData(BaseModel):
    message_id: int




class Response(BaseModel):
    status: str
    retcode: int
    data: MessageData |dict[str,str]
    message: str
    echo: str | None = None
    wording: str
    stream: Literal["stream-action", "normal-action"] | None = None
