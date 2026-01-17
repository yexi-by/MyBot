from app.aiohttp_tools import *  # 这个导入不能删 不然会导致补丁替换失效
from app.core import NapCatServer, MyProvider
from dishka import make_async_container
import uvicorn

container = make_async_container(MyProvider())

napcat = NapCatServer(container=container)


def main():
    uvicorn.run(napcat.app, host="0.0.0.0", port=6055)


if __name__ == "__main__":
    main()
