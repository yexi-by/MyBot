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
from qq_agent_harness.settings import (
    ConversationSettings,
    HarnessSettings,
    ImageSettings,
    ModelEndpoint,
    Policy,
    WorkspaceSettings,
)
from qq_agent_harness.web import create_app
from qq_agent_harness.workspace import WorkspaceService
from qq_agent_harness.workspace import build_tools as build_workspace_tools

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
        workspace=WorkspaceSettings(enabled=True),
    )
    qq_tools = build_qq_tools()
    ws_tools = build_workspace_tools(WorkspaceService(lambda: settings.workspace))
    img_tools = build_image_tools(
        lambda: settings.image,
        reader=reader or MediaReader(resolver=public_ip),
        client=client,
    )
    all_tools = [*qq_tools, *ws_tools, *img_tools]
    categories = {tool.category: True for tool in all_tools}
    policy = Policy(categories=categories)
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

    settings.groups["10"].policy.categories["image"] = False
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
        # 工作区禁用时也不影响纯内存生图与直发
        settings.workspace.enabled = False
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
    assert generated_id in context.generated_media
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

    # 无需开启工作区即可通过 generated_id 直接发送到 QQ 和设置头像
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
    conv = ConversationSession(model=None, scope=GROUP, generated_media=context.generated_media)
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
        assert b'name="image[]"' in edit_req.content
        assert "把两张参考图合成为赛博风格海报".encode() in edit_req.content

        # 验证多图按序号落盘
        assert workspace_files["outputs/poster_1.png"] == WEBP
        assert workspace_files["outputs/poster_2.png"] == PNG

        # 生成后的 generated_id 可继续作为下一轮图生图参考输入（单图使用 image 字段名）
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
        single_edit_req = captured[2]
        assert b'name="image"' in single_edit_req.content
        assert b'name="image[]"' not in single_edit_req.content


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

        # 禁用 workspace__read_file 时禁止读取工作区参考图
        settings.groups["10"].policy.tools["workspace__read_file"] = False
        denied_ws_read = await reg.execute(
            context,
            "image__generate",
            {"prompt": "改图", "images": [{"workspace_path": "a.png"}]},
        )
        assert denied_ws_read.error_type == "PermissionDenied" and not called

        # 禁用 workspace__write_file 时禁止传入 workspace_path 保存
        settings.groups["10"].policy.tools["workspace__write_file"] = False
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
        assert any(item["id"] == "image" for item in catalog["categories"])
        assert any(item["name"] == "image__generate" for item in catalog["tools"])

        models = client.get("/api/image/models")
        assert models.status_code == 200
        assert models.json() == {"models": ["gpt-image-1", "dall-e-3"]}


async def test_openai_images_supports_data_url_response_and_rejects_invalid_data_url() -> None:
    png_b64 = base64.b64encode(PNG).decode("ascii")
    mode = "valid"

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/images/generations"
        if mode == "valid":
            return httpx.Response(200, json={"data": [{"url": f"data:image/png;base64,{png_b64}"}]})
        return httpx.Response(200, json={"data": [{"url": "data:text/plain;base64,aGVsbG8="}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reg, context, _, _ = setup_image_harness(client=http_client)
        ok = await reg.execute(
            context,
            "image__generate",
            {"prompt": "一只蓝猫", "response_format": "url", "aspect_ratio": "16:9"},
        )
        assert ok.status == "ok" and isinstance(ok.data, dict)
        images = ok.data["images"]
        assert isinstance(images, list) and len(images) == 1

        mode = "invalid"
        bad = await reg.execute(
            context,
            "image__generate",
            {"prompt": "非法 data url", "response_format": "url"},
        )
        assert bad.status == "error" and bad.error_type == "ImageGenerationError"


async def test_chat_completions_protocol_handles_cpa_openrouter_and_markdown_formats() -> None:
    png_b64 = base64.b64encode(PNG).decode("ascii")
    webp_b64 = base64.b64encode(WEBP).decode("ascii")
    captured: list[httpx.Request] = []
    call_count = 0

    def serve(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        captured.append(request)
        call_count += 1
        assert request.url == httpx.URL("https://cpa.example/v1/chat/completions")
        if call_count == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "已生成",
                                "images": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/png;base64,{png_b64}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        if call_count == 2:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": f"成品如下：\n![out](data:image/webp;base64,{webp_b64})",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{png_b64}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reg, context, _, _ = setup_image_harness(
            image=ImageSettings(
                enabled=True,
                protocol="chat_completions",
                base_url="https://cpa.example",
                api_key="sk-cpa-secret",
                model="gemini-3.1-flash-image",
            ),
            client=http_client,
        )
        # n=2 时单图协议自动发起第二次请求补齐，且同时覆盖 message.images 与 markdown 图片解析
        res = await reg.execute(
            context,
            "image__generate",
            {"prompt": "夕阳下的海边灯塔", "size": "2k", "aspect_ratio": "16:9", "n": 2},
        )
        assert res.status == "ok" and isinstance(res.data, dict)
        assert res.data["mode"] == "generations"
        images = res.data["images"]
        assert isinstance(images, list) and len(images) == 2
        assert len(captured) == 2
        body = json.loads(captured[0].content.decode("utf-8"))
        assert body == {
            "model": "gemini-3.1-flash-image",
            "stream": False,
            "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": "夕阳下的海边灯塔"}],
            "image_config": {"aspect_ratio": "16:9", "image_size": "2K"},
        }

        # 携带参考图进入图生图多模态 messages，并覆盖 content blocks 响应
        first_id = str(images[0]["generated_id"]) if isinstance(images[0], dict) else ""
        edit_res = await reg.execute(
            context,
            "image__generate",
            {"prompt": "改成冬夜雪景", "images": [{"generated_id": first_id}]},
        )
        assert edit_res.status == "ok" and isinstance(edit_res.data, dict)
        assert edit_res.data["mode"] == "edits"
        edit_body = json.loads(captured[2].content.decode("utf-8"))
        assert edit_body["messages"][0]["content"][0] == {"type": "text", "text": "改成冬夜雪景"}
        assert edit_body["messages"][0]["content"][1] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{png_b64}"},
        }


async def test_responses_protocol_generates_edits_and_reports_failed_status() -> None:
    png_b64 = base64.b64encode(PNG).decode("ascii")
    captured: list[httpx.Request] = []
    fail_next = False

    def serve(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.url == httpx.URL("https://resp.example/v1/responses")
        if fail_next:
            return httpx.Response(
                200,
                json={"status": "failed", "error": {"type": "invalid_request_error"}},
            )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "image_generation_call",
                        "result": png_b64,
                        "revised_prompt": "高清像素风城堡",
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reg, context, _, _ = setup_image_harness(
            image=ImageSettings(
                enabled=True,
                protocol="responses",
                base_url="https://resp.example/v1",
                api_key="sk-resp-secret",
                model="gpt-5.4-mini",
            ),
            client=http_client,
        )
        gen = await reg.execute(
            context,
            "image__generate",
            {
                "prompt": "像素风城堡",
                "size": "1024x1024",
                "quality": "high",
                "output_format": "png",
            },
        )
        assert gen.status == "ok" and isinstance(gen.data, dict)
        body = json.loads(captured[0].content.decode("utf-8"))
        assert body == {
            "model": "gpt-5.4-mini",
            "stream": False,
            "store": False,
            "input": "像素风城堡",
            "tools": [
                {
                    "type": "image_generation",
                    "action": "generate",
                    "size": "1024x1024",
                    "quality": "high",
                    "output_format": "png",
                }
            ],
            "tool_choice": {"type": "image_generation"},
        }
        images = gen.data["images"]
        assert isinstance(images, list) and isinstance(images[0], dict)
        assert images[0]["revised_prompt"] == "高清像素风城堡"

        edit = await reg.execute(
            context,
            "image__generate",
            {"prompt": "加上星空", "images": [{"generated_id": str(images[0]["generated_id"])}]},
        )
        assert edit.status == "ok"
        edit_body = json.loads(captured[1].content.decode("utf-8"))
        assert edit_body["tools"][0]["action"] == "edit"
        assert edit_body["input"] == [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "加上星空"},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{png_b64}"},
                ],
            }
        ]

        fail_next = True
        failed = await reg.execute(context, "image__generate", {"prompt": "触发失败"})
        assert failed.status == "error" and failed.error_type == "ImageGenerationError"
        assert "sk-resp-secret" not in (failed.message or "")


async def test_gemini_protocol_uses_header_auth_skips_thought_images_and_validates_model() -> None:
    png_b64 = base64.b64encode(PNG).decode("ascii")
    webp_b64 = base64.b64encode(WEBP).decode("ascii")
    captured: list[httpx.Request] = []
    mode = "ok"

    def serve(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if mode == "blocked":
            return httpx.Response(
                200,
                json={"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]},
            )
        if mode == "invalid_arg":
            return httpx.Response(
                400,
                json={
                    "error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "sk-gem-secret"}
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {
                                    "thought": True,
                                    "inlineData": {"mimeType": "image/webp", "data": webp_b64},
                                },
                                {"inlineData": {"mimeType": "image/png", "data": png_b64}},
                            ]
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reg, context, settings, _ = setup_image_harness(
            image=ImageSettings(
                enabled=True,
                protocol="gemini",
                base_url="https://generativelanguage.googleapis.com",
                api_key="sk-gem-secret",
                model="models/gemini-3.1-flash-image-preview",
            ),
            client=http_client,
        )
        res = await reg.execute(
            context,
            "image__generate",
            {"prompt": "雨夜赛博街景", "size": "2k", "aspect_ratio": "16:9"},
        )
        assert res.status == "ok" and isinstance(res.data, dict)
        req = captured[0]
        assert req.url == httpx.URL(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image-preview:generateContent"
        )
        assert "key=" not in str(req.url)
        assert req.headers["x-goog-api-key"] == "sk-gem-secret"
        assert "authorization" not in req.headers
        body = json.loads(req.content.decode("utf-8"))
        assert body == {
            "contents": [{"role": "user", "parts": [{"text": "雨夜赛博街景"}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"},
            },
        }
        # 应跳过 thought=True 的 WebP 草图，只返回最终 PNG
        images = res.data["images"]
        assert isinstance(images, list) and len(images) == 1 and isinstance(images[0], dict)
        assert images[0]["mime_type"] == "image/png"

        # 携带参考图时以 inlineData 传入
        edit = await reg.execute(
            context,
            "image__generate",
            {"prompt": "改成晴天", "images": [{"generated_id": str(images[0]["generated_id"])}]},
        )
        assert edit.status == "ok"
        edit_body = json.loads(captured[1].content.decode("utf-8"))
        assert edit_body["contents"][0]["parts"][1] == {
            "inlineData": {"mimeType": "image/png", "data": png_b64}
        }

        # 安全拦截不可重试
        mode = "blocked"
        blocked = await reg.execute(context, "image__generate", {"prompt": "拦截测试"})
        assert blocked.status == "error" and "IMAGE_SAFETY" in (blocked.message or "")
        assert len(captured) == 3

        # 400 INVALID_ARGUMENT 不重试且不泄漏密钥
        mode = "invalid_arg"
        invalid = await reg.execute(context, "image__generate", {"prompt": "参数错误"})
        assert invalid.status == "error"
        assert "INVALID_ARGUMENT" in (invalid.message or "")
        assert "sk-gem-secret" not in (invalid.message or "")
        assert len(captured) == 4

        # 非法模型名在请求前拦截
        settings.image.model = "../evil?key=leak"
        bad_model = await reg.execute(context, "image__generate", {"prompt": "路径注入"})
        assert bad_model.status == "error" and len(captured) == 4


async def test_xai_ark_and_dashscope_protocols_send_native_payloads() -> None:
    png_b64 = base64.b64encode(PNG).decode("ascii")
    webp_b64 = base64.b64encode(WEBP).decode("ascii")
    captured: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.host == "api.x.ai":
            return httpx.Response(200, json={"data": [{"b64_json": png_b64}]})
        if request.url.host == "ark.cn-beijing.volces.com":
            return httpx.Response(200, json={"data": [{"b64_json": webp_b64}]})
        if request.url.host == "dashscope.aliyuncs.com":
            body_text = request.content.decode("utf-8")
            if "ds-error" in body_text:
                return httpx.Response(
                    200,
                    json={"code": "InvalidParameter", "message": "bad size sk-ds"},
                )
            if "results-format" in body_text:
                return httpx.Response(
                    200,
                    json={"output": {"results": [{"url": "https://cdn.dashscope.example/r.png"}]}},
                )
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "content": [{"image": "https://cdn.dashscope.example/c.webp"}]
                                }
                            }
                        ]
                    }
                },
            )
        if request.url.path == "/c.webp" and request.headers.get("host") == "cdn.dashscope.example":
            return httpx.Response(200, content=WEBP, headers={"content-type": "image/webp"})
        if request.url.path == "/r.png" and request.headers.get("host") == "cdn.dashscope.example":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http_client:
        reader = MediaReader(client=http_client, resolver=public_ip)

        # 1) xAI Grok Imagine：文生图映射 size -> aspect_ratio/resolution；单图 image vs 多图 images 互斥
        reg, context, settings, _ = setup_image_harness(
            image=ImageSettings(
                enabled=True,
                protocol="xai_images",
                base_url="https://api.x.ai",
                api_key="sk-xai",
                model="grok-imagine-image-2.0",
            ),
            reader=reader,
            client=http_client,
        )
        xai_gen = await reg.execute(
            context,
            "image__generate",
            {"prompt": "机械猫", "size": "2048x2048", "quality": "medium"},
        )
        assert xai_gen.status == "ok" and isinstance(xai_gen.data, dict)
        xai_body = json.loads(captured[-1].content.decode("utf-8"))
        assert captured[-1].url == httpx.URL("https://api.x.ai/v1/images/generations")
        assert xai_body == {
            "model": "grok-imagine-image-2.0",
            "prompt": "机械猫",
            "n": 1,
            "aspect_ratio": "1:1",
            "resolution": "2k",
            "quality": "medium",
        }
        assert "size" not in xai_body
        xai_images = xai_gen.data["images"]
        assert isinstance(xai_images, list) and isinstance(xai_images[0], dict)
        g1 = str(xai_images[0]["generated_id"])

        xai_edit_one = await reg.execute(
            context,
            "image__generate",
            {"prompt": "单图修改", "images": [{"generated_id": g1}]},
        )
        assert xai_edit_one.status == "ok"
        one_body = json.loads(captured[-1].content.decode("utf-8"))
        assert captured[-1].url == httpx.URL("https://api.x.ai/v1/images/edits")
        assert "image" in one_body and "images" not in one_body
        assert one_body["image"]["type"] == "image_url"

        xai_edit_two = await reg.execute(
            context,
            "image__generate",
            {"prompt": "双图融合", "images": [{"generated_id": g1}, {"generated_id": g1}]},
        )
        assert xai_edit_two.status == "ok"
        two_body = json.loads(captured[-1].content.decode("utf-8"))
        assert "images" in two_body and "image" not in two_body
        assert len(two_body["images"]) == 2

        # 2) 火山方舟 Ark Seedream：自动补 /api/v3；图生图仍走 /images/generations，不传 n，n=2 循环补齐
        settings.image = ImageSettings(
            enabled=True,
            protocol="ark_images",
            base_url="https://ark.cn-beijing.volces.com",
            api_key="sk-ark",
            model="doubao-seedream-5-0-lite",
        )
        ark_res = await reg.execute(
            context,
            "image__generate",
            {"prompt": "水墨江南", "images": [{"generated_id": g1}], "size": "2K", "n": 2},
        )
        assert ark_res.status == "ok" and isinstance(ark_res.data, dict)
        assert isinstance(ark_res.data["images"], list) and len(ark_res.data["images"]) == 2
        ark_req = captured[-1]
        assert ark_req.url == httpx.URL(
            "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        )
        ark_body = json.loads(ark_req.content.decode("utf-8"))
        assert ark_body == {
            "model": "doubao-seedream-5-0-lite",
            "prompt": "水墨江南",
            "image": f"data:image/png;base64,{png_b64}",
            "size": "2K",
        }
        assert "n" not in ark_body

        # 3) 阿里百炼 DashScope 原生：size 转 *，兼容 choices 与 results 响应格式
        settings.image = ImageSettings(
            enabled=True,
            protocol="dashscope",
            base_url="https://dashscope.aliyuncs.com",
            api_key="sk-ds",
            model="qwen-image-2.0-pro",
        )
        ds_res = await reg.execute(
            context,
            "image__generate",
            {"prompt": "古风插画", "images": [{"generated_id": g1}], "size": "1024x1536", "n": 2},
        )
        assert ds_res.status == "ok"
        ds_req = captured[-2]
        assert ds_req.url == httpx.URL(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )
        ds_body = json.loads(ds_req.content.decode("utf-8"))
        assert ds_body == {
            "model": "qwen-image-2.0-pro",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": f"data:image/png;base64,{png_b64}"},
                            {"text": "古风插画"},
                        ],
                    }
                ]
            },
            "parameters": {"n": 2, "size": "1024*1536"},
        }

        ds_res2 = await reg.execute(context, "image__generate", {"prompt": "results-format"})
        assert ds_res2.status == "ok" and isinstance(ds_res2.data, dict)
        ds_images2 = ds_res2.data["images"]
        assert isinstance(ds_images2, list) and isinstance(ds_images2[0], dict)
        assert ds_images2[0]["mime_type"] == "image/png"

        before_err = len(captured)
        ds_err = await reg.execute(context, "image__generate", {"prompt": "ds-error"})
        assert ds_err.status == "error"
        assert "InvalidParameter" in (ds_err.message or "")
        assert "sk-ds" not in (ds_err.message or "")
        assert len(captured) == before_err + 1


def test_webui_gemini_image_models_endpoint_strips_models_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://generativelanguage.googleapis.com/v1beta/models")
        assert request.headers["x-goog-api-key"] == "sk-gem-key"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/gemini-2.5-flash-image"},
                    {"name": "models/gemini-3.1-flash-image-preview"},
                ]
            },
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
protocol = "gemini"
base_url = "https://generativelanguage.googleapis.com"
api_key = "sk-gem-key"
model = "gemini-3.1-flash-image-preview"
""".strip(),
        encoding="utf-8",
    )
    app = create_app(config_file, connect_mcp=False)
    with TestClient(app) as client:
        models = client.get("/api/image/models")
        assert models.status_code == 200
        assert models.json() == {
            "models": ["gemini-2.5-flash-image", "gemini-3.1-flash-image-preview"]
        }


def test_image_settings_omits_auth_headers_when_api_key_empty() -> None:
    openai_cfg = ImageSettings(protocol="openai_images", api_key="")
    assert "Authorization" not in openai_cfg.request_headers()

    gemini_cfg = ImageSettings(protocol="gemini", api_key="  ")
    assert "x-goog-api-key" not in gemini_cfg.request_headers()

    gemini_bearer = ImageSettings(
        protocol="gemini",
        api_key="sk-gem",
        headers={"Authorization": "Bearer custom-token"},
    )
    assert gemini_bearer.request_headers() == {"Authorization": "Bearer custom-token"}
