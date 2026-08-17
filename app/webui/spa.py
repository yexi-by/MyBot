"""前端构建产物的 SPA 挂载。"""

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

WEBUI_DIST_DIRECTORY = Path(__file__).resolve().parents[2] / "webui" / "dist"


class SPAStaticFiles(StaticFiles):
    """未命中的路径回退到 index.html，交给前端路由处理。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """先按静态文件处理，404 时回退到 SPA 入口。"""
        request_path = str(scope.get("path", "")).lstrip("/")
        if request_path == "api" or request_path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def mount_webui_static(app: FastAPI, *, dist_dir: Path = WEBUI_DIST_DIRECTORY) -> None:
    """挂载前端构建产物；产物缺失时挂载构建提示。"""
    if (dist_dir / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=dist_dir, html=True), name="webui-spa")
        return

    @app.get("/", include_in_schema=False)
    def webui_not_built() -> dict[str, str]:
        """前端产物缺失时的占位响应。"""
        return {"message": "WebUI 前端尚未构建，请先执行 cd webui && npm run build"}

    _ = webui_not_built
