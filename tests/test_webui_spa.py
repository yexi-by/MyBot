"""WebUI SPA 静态挂载测试。"""

import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.webui.spa import mount_webui_static


class WebUISPATest(unittest.IsolatedAsyncioTestCase):
    """验证前端路由回退不会吞掉不存在的 API。"""

    async def test_spa_fallback_excludes_api_paths(self) -> None:
        """客户端页面路径回退 index，未知 API 仍返回 404。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir)
            (dist_dir / "index.html").write_text(
                "<!doctype html><title>WebUI</title>", encoding="utf-8"
            )
            app = FastAPI()
            mount_webui_static(app, dist_dir=dist_dir)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://webui.test",
            ) as client:
                page_response = await client.get("/settings/providers")
                api_response = await client.get("/api/missing")

        self.assertEqual(page_response.status_code, 200)
        self.assertIn("<title>WebUI</title>", page_response.text)
        self.assertEqual(api_response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
