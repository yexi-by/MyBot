from pydantic import BaseModel, Field
from app.models import GroupMessage, PrivateMessage, Notice, Meta, Request


class SessionData(BaseModel):
    msg_data: dict[int, GroupMessage | PrivateMessage] = Field(default_factory=dict)
    time_map: dict[int, int] = Field(default_factory=dict)


class Data(BaseModel):
    group: dict[int, SessionData] = Field(default_factory=dict)
    private: dict[int, SessionData] = Field(default_factory=dict)
    notice: list[Notice] = Field(default_factory=list)
    meta: list[Meta] = Field(default_factory=list)
    request: list[Request] = Field(default_factory=list)
