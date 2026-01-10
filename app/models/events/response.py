from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MessageData(BaseModel):
    message_id: int


class StreamData(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["stream", "response", "error"]
    data_type: str


class StreamDataChunk(StreamData):
    type: Literal["stream"] = "stream"
    data_type: Literal["data_chunk", "file_chunk"]
    data: str
    index: int | None = None
    size: int | None = None
    progress: int | None = None
    base64_size: int | None = None


class StreamDataInfo(StreamData):
    type: Literal["stream"] = "stream"
    data_type: Literal["file_info"]
    file_name: str | None = None
    file_size: int | None = None
    chunk_size: int | None = None


class StreamDataComplete(StreamData):
    type: Literal["response"] = "response"
    data_type: Literal["data_complete", "file_complete"]
    data: str | Literal["Stream transmission complete"] | None = None
    total_chunks: int | None = None
    total_bytes: int | None = None
    message: str | None = None


class StreamDataError(StreamData):
    type: Literal["error"] = "error"
    data_type: Literal["error"]
    message: str | None = None
    data: Any | None = None


StreamDataPayload = (
    StreamDataChunk | StreamDataInfo | StreamDataComplete | StreamDataError
)


class Response(BaseModel):
    status: str
    retcode: int
    data: MessageData | StreamDataPayload | dict = Field(union_mode="smart")
    message: str
    echo: str | None = None
    wording: str
    stream: Literal["stream-action", "normal-action"] | None = None
