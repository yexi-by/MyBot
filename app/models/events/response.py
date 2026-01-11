from typing import  Literal

from pydantic import BaseModel


class Response(BaseModel):
    status: str
    retcode: int
    data: dict
    message: str
    echo: str | None = None
    wording: str
    stream: Literal["stream-action", "normal-action"] | None = None
