import aiohttp.streams

# 猴子补丁 修改aiohttp的64kb限制
_original_stream_reader_init = aiohttp.streams.StreamReader.__init__


def _new_stream_reader_init(self, protocol, limit, *args, **kwargs):
    new_limit = 1024 * 1024 * 1024
    if limit < new_limit:
        limit = new_limit
    _original_stream_reader_init(self, protocol, limit, *args, **kwargs)


aiohttp.streams.StreamReader.__init__ = _new_stream_reader_init
from app.core import NapCatServer, MyProvider
from dishka import make_async_container
import uvicorn

container = make_async_container(MyProvider())

napcat = NapCatServer(container=container)


def main():
    uvicorn.run(napcat.app, host="0.0.0.0", port=6055)


if __name__ == "__main__":
    uvicorn.run(napcat.app, host="0.0.0.0", port=6055)
