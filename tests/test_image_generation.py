"""独立图像生成工具、参考图权限边界、直发联动与模型列表接口的行为回归。"""

import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_napcat import GROUP, Bot, message

from qq_agent_harness.contracts import ConversationScope, JsonObject, ToolContext
from qq_agent_harness.image import CATEGORY_LABELS as IMAGE_CATEGORIES
from qq_agent_harness.image import build_tools as build_image_tools
from qq_agent_harness.napcat import MediaReader
from qq_agent_harness.napcat import build_tools as build_qq_tools
from qq_agent_harness.providers import ModelSession, ResponseGroup, ToolCall
from qq_agent_harness.registry import ToolRegistry
from qq_agent_harness.runtime import ConversationSession
from qq_agent_harness.sandbox import RunnerClient
from qq_agent_harness.sandbox import build_tools as build_sandbox_tools
from qq_agent_harness.settings import (
    ConversationSettings,
    HarnessSettings,
    ImageSettings,
    ModelEndpoint,
    Policy,
    SandboxSettings,
)
from qq_agent_harness.web import create_app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="
)
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x000\x01\x00\x9d\x01*\x01\x00\x01\x00\x02\x004%\xa4\x00\x03p\x00\xfb\xfd\x00"


async def public_ip(_: str) -> str:
    return "8.8.8.8"


def setup_image_harness(
    bot: Bot | None = None,
    *,
    image: ImageSettings | None = None,
    scope: ConversationScope = GROUP,
    reader: MediaReader | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[ToolRegistry, ToolContext, HarnessSettings, Bot]:
    active_bot = bot or Bot()
    settings = HarnessSettings(
        image=image
        or ImageSettings(
            enabled=True,
            base_url="https://image.example/v1",
            api_key="sk-image-secret-token",
            model="gpt-image-1",
            max_retries=1,
            headers={"X-Custom-Source": "mybot"},
        ),
        sandbox=SandboxSettings(enabled=True),
    )
    qq_tools = build_qq_tools()
    sb_tools = build_sandbox_tools(lambda: settings.sandbox, RunnerClient(lambda: settings.sandbox))
    img_tools = build_image_tools(
        lambda: settings.image,
        reader=reader or MediaReader(resolver=public_ip),
        client=client,
    )
    all_tools = [*qq_tools, *sb_tools, *img_tools]
    categories = {tool.category: True for tool in all_tools}
    policy = Policy(categories=categories, max_sends_per_turn=5)
    settings.groups["10"] = ConversationSettings(enabled=True, policy=policy)
    settings.private = ConversationSettings(enabled=True, policy=policy)

    reg = ToolRegistry(lambda: settings)
    reg.describe_categories(IMAGE_CATEGORIES)
    for tool in all_tools:
        reg.add(tool)
    context = ToolContext(scope, "test-turn", active_bot)
    return reg, context, settings, active_bot


async def test_image_tool_visibility_requires_enabled_service_and_policy() -> None:
    reg, context, settings, _ = setup_image_harness(
        image=ImageSettings(enabled=False, base_url="https://image.example/v1", model="gpt-image-1")
    )
    assert "image__generate" not in reg.available(context.scope)
    denied = await reg.execute(context, "image__generate", {"prompt": "一只猫"})
    assert denied.error_type == "PermissionDenied"

    settings.image.enabled = True
    settings.image.model = ""
    assert "image__generate" not in reg.available(context.scope)

    settings.image.model = "gpt-image-1"
    assert "image__generate" in reg.available(context.scope)

    settings.groups["10"].policy.categories["media.generate"] = False
    assert "image__generate" not in reg.available(context.scope)


async def test_text_to_image_forwards_parameters_attaches_multimodal_and_sends_to_qq() -> None:
    captured: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/v1/images/generations":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "b64_json": base64.b64encode(PNG).decode("ascii"),
                            "revised_prompt": "一只戴宇航头盔的橘猫",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected path: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reg, context, settings, bot = setup_image_harness(client=http_client)
        # 沙箱禁用时也不影响纯内存生图与直发
        settings.sandbox.enabled = False
        result = await reg.execute(
            context,
            "image__generate",
            {
                "prompt": "画一只太空橘猫",
                "size": "1024x1024",
                "quality": "high",
                "background": "transparent",
                "output_format": "png",
                "inspect": True,
            },
        )
    assert result.status == "ok" and isinstance(result.data, dict)
    assert result.data["mode"] == "generations"
    assert len(captured) == 1
    req = captured[0]
    assert req.headers["authorization"] == "Bearer sk-image-secret-token"
    assert req.headers["x-custom-source"] == "mybot"
    payload = json.loads(req.content.decode("utf-8"))
    assert payload == {
        "model": "gpt-image-1",
        "prompt": "画一只太空橘猫",
        "n": 1,
        "size": "1024x1024",
        "quality": "high",
        "background": "transparent",
        "output_format": "png",
    }

    images = result.data["images"]
    assert isinstance(images, list) and len(images) == 1 and isinstance(images[0], dict)
    generated_id = str(images[0]["generated_id"])
    assert generated_id in context.generated_images
    assert images[0]["mime_type"] == "image/png"
    assert images[0]["revised_prompt"] == "一只戴宇航头盔的橘猫"

    # 验证 ModelSession.result() 会把 inspect=True 产出的 media 注入多模态上下文并从 JSON 文本剥离 base64
    session = ModelSession(
        ModelEndpoint(
            protocol="chat_completions",
            api_key="k",
            base_url="https://llm.example/v1",
            model="gpt-5.4",
        ),
        "system",
        "wake",
    )
    call = ToolCall(id="call-1", name="image__generate", arguments={"prompt": "画一只太空橘猫"})
    session._groups.append(ResponseGroup(items=[], expected={"call-1"}, calls=[call]))
    session.result(call, result)
    session.flush_media()
    assert any(
        isinstance(content := item.get("content"), list)
        and any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)
        for item in session.items
    )
    tool_items = [item for item in session.items if item.get("role") == "tool"]
    assert len(tool_items) == 1
    assert base64.b64encode(PNG).decode("ascii") not in str(tool_items[0].get("content"))

    # 无需开启沙箱即可通过 generated_id 直接发送到 QQ 和设置头像
    send_res = await reg.execute(
        context,
        "qq__send_message",
        {"parts": [{"type": "image", "generated_id": generated_id}]},
    )
    assert send_res.status == "ok"
    action, params = bot.calls[-1]
    assert action == "send_group_msg"
    expected_uri = "base64://" + base64.b64encode(PNG).decode("ascii")
    assert params["message"] == [{"type": "image", "data": {"file": expected_uri}}]

    avatar_res = await reg.execute(
        context,
        "qq__set_group_portrait",
        {"generated_id": generated_id},
    )
    assert avatar_res.status == "ok"
    assert bot.calls[-1] == ("set_group_portrait", {"group_id": "10", "file": expected_uri})

    # 会话容量统计包含生图缓存
    conv = ConversationSession(model=None, scope=GROUP, generated_images=context.generated_images)
    assert conv.measure() > len(PNG)


async def test_image_edits_combines_qq_workspace_and_generated_sources_and_writes_workspace() -> (
    None
):
    captured: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/v1/images/edits":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"url": "https://cdn.image.example/out1.webp"},
                        {"b64_json": base64.b64encode(PNG).decode("ascii")},
                    ]
                },
            )
        if request.url.path == "/out1.webp":
            return httpx.Response(200, content=WEBP, headers={"content-type": "image/webp"})
        raise AssertionError(f"unexpected request: {request.url}")

    picture_msg = message("30")
    picture_msg["message"] = [
        {
            "type": "image",
            "data": {"file": "qq.png", "base64": base64.b64encode(PNG).decode("ascii")},
        }
    ]

    async def bot_handler(action: str, _: JsonObject) -> JsonObject:
        if action == "get_msg":
            return picture_msg
        return {"message_id": "99"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reader = MediaReader(client=http_client, resolver=public_ip)
        reg, context, _, _ = setup_image_harness(
            Bot(bot_handler), reader=reader, client=http_client
        )

        workspace_files: dict[str, bytes] = {"refs/template.webp": WEBP}

        async def read_ws(path: str) -> bytes:
            return workspace_files[path]

        async def write_ws(path: str, content: bytes) -> None:
            workspace_files[path] = content

        context.read_workspace = read_ws
        context.write_workspace = write_ws

        result = await reg.execute(
            context,
            "image__generate",
            {
                "prompt": "把两张参考图合成为赛博风格海报",
                "images": [
                    {"message_id": "30"},
                    {"workspace_path": "refs/template.webp"},
                ],
                "n": 2,
                "inspect": False,
                "workspace_path": "outputs/poster.png",
            },
        )
        assert result.status == "ok" and isinstance(result.data, dict)
        assert result.data["mode"] == "edits"
        assert "media" not in result.data
        assert len(captured) == 2
        edit_req = captured[0]
        assert edit_req.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="image"' in edit_req.content
        assert "把两张参考图合成为赛博风格海报".encode() in edit_req.content

        # 验证多图按序号落盘
        assert workspace_files["outputs/poster_1.png"] == WEBP
        assert workspace_files["outputs/poster_2.png"] == PNG

        # 生成后的 generated_id 可继续作为下一轮图生图参考输入
        images = result.data["images"]
        assert isinstance(images, list) and isinstance(images[0], dict)
        first_id = str(images[0]["generated_id"])
        second = await reg.execute(
            context,
            "image__generate",
            {
                "prompt": "继续微调细节",
                "images": [{"generated_id": first_id}],
                "inspect": False,
            },
        )
        assert second.status == "ok"


async def test_reference_and_workspace_permissions_are_enforced_before_upstream_request() -> None:
    called = False

    def serve(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reg, context, settings, _ = setup_image_harness(client=http_client)
        # 禁用 qq__read_media 时禁止读取 QQ 消息图作为参考图
        settings.groups["10"].policy.tools["qq__read_media"] = False
        denied_qq = await reg.execute(
            context,
            "image__generate",
            {"prompt": "改图", "images": [{"message_id": "30"}]},
        )
        assert denied_qq.error_type == "PermissionDenied" and not called

        # 禁用 sandbox__read_file 时禁止读取工作区参考图
        settings.groups["10"].policy.tools["sandbox__read_file"] = False
        denied_ws_read = await reg.execute(
            context,
            "image__generate",
            {"prompt": "改图", "images": [{"workspace_path": "a.png"}]},
        )
        assert denied_ws_read.error_type == "PermissionDenied" and not called

        # 禁用 sandbox__write_file 时禁止传入 workspace_path 保存
        settings.groups["10"].policy.tools["sandbox__write_file"] = False
        denied_ws_write = await reg.execute(
            context,
            "image__generate",
            {"prompt": "生图", "workspace_path": "out.png"},
        )
        assert denied_ws_write.error_type == "PermissionDenied" and not called


async def test_image_retry_and_error_sanitization_never_leak_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def serve(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, text="rate limit sk-image-secret-token")
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(PNG).decode("ascii")}]},
        )

    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("qq_agent_harness.image.asyncio.sleep", fast_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reg, context, _, _ = setup_image_harness(client=http_client)
        ok = await reg.execute(context, "image__generate", {"prompt": "重试生图", "inspect": False})
        assert ok.status == "ok" and attempts == 2

    def serve_401(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key sk-image-secret-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve_401)) as http_client:
        reg, context, _, _ = setup_image_harness(client=http_client)
        failed = await reg.execute(context, "image__generate", {"prompt": "鉴权失败"})
        assert failed.status == "error"
        assert "sk-image-secret-token" not in (failed.message or "")
        assert "401" in (failed.message or "")


def test_webui_image_models_endpoint_and_bootstrap_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://image.example/v1/models")
        assert request.headers["authorization"] == "Bearer sk-img-key"
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-image-1"}, {"id": "dall-e-3"}]},
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "qq_agent_harness.web.httpx.AsyncClient",
        lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(serve)}),
    )

    (tmp_path / "persona.md").write_text("测试人格", encoding="utf-8")
    config_file = tmp_path / "harness.toml"
    config_file.write_text(
        """
[image]
enabled = true
base_url = "https://image.example/v1"
api_key = "sk-img-key"
model = "gpt-image-1"
""".strip(),
        encoding="utf-8",
    )
    app = create_app(config_file, connect_mcp=False)
    with TestClient(app) as client:
        catalog_resp = client.get("/api/tools")
        assert catalog_resp.status_code == 200
        catalog = catalog_resp.json()
        assert any(item["id"] == "media.generate" for item in catalog["categories"])
        assert any(item["name"] == "image__generate" for item in catalog["tools"])

        models = client.get("/api/image/models")
        assert models.status_code == 200
        assert models.json() == {"models": ["gpt-image-1", "dall-e-3"]}
