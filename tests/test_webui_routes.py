"""WebUI 配置 API 路由测试。"""

import asyncio
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import httpx
import uvicorn

from app.webui import PowerController
from app.webui.dev import create_dev_app


def base_config() -> str:
    """生成不依赖外部文件的最小配置。"""
    return textwrap.dedent(
        """
        [server]
        port = 6055

        [napcat]
        websocket_token = "test-token"

        [database]
        password = "test-password"

        [llm.providers.main]
        api_key = "test-api-key"
        max_attempts = 3
        retry_delay_seconds = 0

        [plugins.group_notice]
        groups = ["40000"]
        send_avatar = true
        """
    ).strip() + "\n"


class WebUIRoutesTest(unittest.IsolatedAsyncioTestCase):
    """通过 dev app 工厂验证各端点状态码与契约。"""

    def _client(
        self, root: Path, *, power: PowerController | None = None
    ) -> httpx.AsyncClient:
        app = create_dev_app(config_file=root / "mybot.toml", power=power)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://webui.test"
        )

    async def test_get_config_returns_payload_and_meta(self) -> None:
        """GET /api/config 返回原始配置、哈希与运行态元信息。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(base_config(), encoding="utf-8")
            async with self._client(root) as client:
                response = await client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["issues"], [])
        self.assertEqual(body["config"]["server"]["port"], 6055)
        self.assertEqual(body["config"]["server"]["host"], "0.0.0.0")
        self.assertEqual(body["config"]["napcat"]["websocket_token"], "test-token")
        self.assertEqual(len(body["sha256"]), 64)
        self.assertEqual(body["meta"]["plugin_revision"], 1)
        self.assertFalse(body["meta"]["watcher_active"])
        self.assertIn("server", body["meta"]["restart_only_sections"])
        self.assertEqual(body["meta"]["restart_required_sections"], [])
        self.assertTrue(body["meta"]["boot_id"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_validate_reports_issues_without_writing(self) -> None:
        """POST /api/config/validate 只校验不落盘。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "mybot.toml"
            original = base_config()
            config_file.write_text(original, encoding="utf-8")
            async with self._client(root) as client:
                get_response = await client.get("/api/config")
                payload: dict[str, Any] = get_response.json()["config"]
                payload["server"]["port"] = 70000
                response = await client.post(
                    "/api/config/validate", json={"config": payload}
                )

            self.assertEqual(config_file.read_text(encoding="utf-8"), original)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["issues"][0]["location"], "server.port")

    async def test_put_config_writes_file_and_reports_restart(self) -> None:
        """PUT /api/config 写回文件并标注待重启节。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "mybot.toml"
            config_file.write_text(base_config(), encoding="utf-8")
            async with self._client(root) as client:
                get_response = await client.get("/api/config")
                current = get_response.json()
                payload: dict[str, Any] = current["config"]
                payload["server"]["port"] = 7000
                payload["plugins"]["group_notice"]["groups"] = ["50000"]
                response = await client.put(
                    "/api/config",
                    json={"config": payload, "base_sha256": current["sha256"]},
                )

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["config"]["server"]["port"], 7000)
                self.assertEqual(body["restart_required_sections"], ["server"])

                reread = await client.get("/api/config")

        self.assertEqual(reread.json()["config"]["server"]["port"], 7000)
        self.assertEqual(
            reread.json()["config"]["plugins"]["group_notice"]["groups"], ["50000"]
        )

    async def test_put_config_conflict_returns_409(self) -> None:
        """过期哈希保存返回 409。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(base_config(), encoding="utf-8")
            async with self._client(root) as client:
                get_response = await client.get("/api/config")
                response = await client.put(
                    "/api/config",
                    json={
                        "config": get_response.json()["config"],
                        "base_sha256": "0" * 64,
                    },
                )

        self.assertEqual(response.status_code, 409)

    async def test_put_config_invalid_payload_returns_422(self) -> None:
        """非法配置返回 422 与逐字段错误。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(base_config(), encoding="utf-8")
            async with self._client(root) as client:
                get_response = await client.get("/api/config")
                current = get_response.json()
                payload: dict[str, Any] = current["config"]
                payload["server"]["port"] = 70000
                response = await client.put(
                    "/api/config",
                    json={"config": payload, "base_sha256": current["sha256"]},
                )

        self.assertEqual(response.status_code, 422)
        issues = response.json()["detail"]["issues"]
        self.assertEqual(issues[0]["location"], "server.port")

    async def test_put_config_read_only_returns_clear_503(self) -> None:
        """只读挂载导致写入失败时返回可理解的错误，而不是泄漏系统异常。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(base_config(), encoding="utf-8")
            async with self._client(root) as client:
                current = (await client.get("/api/config")).json()
                with patch(
                    "app.webui.routes.config_io.write_config_payload",
                    side_effect=PermissionError,
                ):
                    response = await client.put(
                        "/api/config",
                        json={
                            "config": current["config"],
                            "base_sha256": current["sha256"],
                        },
                    )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "配置文件不可写，请检查 config 目录挂载权限",
        )

    async def test_files_roundtrip_and_guards(self) -> None:
        """文件列表、读取、写回、逃逸与冲突的完整链路。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "config"
            root.mkdir()
            (root / "mybot.toml").write_text(base_config(), encoding="utf-8")
            (root / "prompts").mkdir()
            (root / "prompts/system.md").write_text("角色", encoding="utf-8")
            (root.parent / "escape.md").write_text("外部", encoding="utf-8")
            async with self._client(root) as client:
                list_response = await client.get("/api/files")
                self.assertEqual(list_response.json()["files"], ["prompts/system.md"])

                read_response = await client.get("/api/files/prompts/system.md")
                self.assertEqual(read_response.status_code, 200)
                file_body = read_response.json()
                self.assertEqual(file_body["content"], "角色")

                save_response = await client.put(
                    "/api/files/prompts/system.md",
                    json={"content": "新角色", "base_sha256": file_body["sha256"]},
                )
                self.assertEqual(save_response.status_code, 200)

                conflict_response = await client.put(
                    "/api/files/prompts/system.md",
                    json={"content": "再次", "base_sha256": file_body["sha256"]},
                )
                self.assertEqual(conflict_response.status_code, 409)

                escape_response = await client.get("/api/files/..%2Fescape.md")
                self.assertEqual(escape_response.status_code, 422)

                missing_response = await client.get("/api/files/prompts/none.md")
                self.assertEqual(missing_response.status_code, 404)

                config_bypass = await client.put(
                    "/api/files/mybot.toml",
                    json={"content": "[server]\nport = 1\n", "base_sha256": None},
                )
                self.assertEqual(config_bypass.status_code, 422)
                config_read_bypass = await client.get("/api/files/mybot.toml")
                self.assertEqual(config_read_bypass.status_code, 422)

                reread = await client.get("/api/files/prompts/system.md")

        self.assertEqual(reread.json()["content"], "新角色")
        self.assertEqual(reread.headers["cache-control"], "no-store")

    async def test_power_actions_trigger_graceful_exit(self) -> None:
        """重启与关机端点受理后延迟触发 uvicorn 优雅停机。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(base_config(), encoding="utf-8")
            power = PowerController(delay_seconds=0.05)
            namespace = SimpleNamespace(should_exit=False)
            power.bind(cast(uvicorn.Server, namespace))
            async with self._client(root, power=power) as client:
                restart_response = await client.post("/api/system/restart")
                shutdown_response = await client.post("/api/system/shutdown")
                for _ in range(100):
                    if namespace.should_exit:
                        break
                    await asyncio.sleep(0.02)

        self.assertEqual(restart_response.status_code, 202)
        self.assertEqual(restart_response.json()["action"], "restart")
        self.assertTrue(restart_response.json()["ok"])
        self.assertEqual(shutdown_response.status_code, 202)
        self.assertEqual(shutdown_response.json()["action"], "shutdown")
        self.assertTrue(namespace.should_exit)

    async def test_power_actions_unavailable_without_binding(self) -> None:
        """未绑定电源控制时电源端点返回 503。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mybot.toml").write_text(base_config(), encoding="utf-8")
            async with self._client(root) as client:
                restart_response = await client.post("/api/system/restart")
                shutdown_response = await client.post("/api/system/shutdown")

        self.assertEqual(restart_response.status_code, 503)
        self.assertEqual(shutdown_response.status_code, 503)
        self.assertEqual(
            restart_response.json()["detail"], "当前运行模式未启用电源控制"
        )


if __name__ == "__main__":
    unittest.main()
