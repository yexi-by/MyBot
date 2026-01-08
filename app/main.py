from app.core import NapCatServer, MyProvider
from dishka import make_async_container
import uvicorn
container = make_async_container(MyProvider())

napcat = NapCatServer(container=container)
def main():
    uvicorn.run(napcat.app, host="0.0.0.0", port=6055)

if __name__ == "__main__":
    uvicorn.run(napcat.app, host="0.0.0.0", port=6055)
