"""Neavo 群聊生图插件的配置、协议与并发行为测试。"""

from __future__ import annotations

import asyncio
import base64
import json
import unittest
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.config import NeavoImageGenerateConfig
from app.database import GroupDataScope, StoredGroupMessage
from app.models import (
    At,
    GroupMessage,
    Image,
    MessageSegment,
    Reply,
    Response,
    Sender,
    Text,
)
from app.plugins.base import Context
from app.plugins.neavo_image_generate.client import (
    MAX_CONSECUTIVE_POLL_RETRIES,
    MAX_INPUT_IMAGE_BYTES,
    NeavoGenerationTimeoutError,
    NeavoImageClient,
    NeavoProtocolError,
    NeavoTransportError,
    NeavoUpstreamError,
)
from app.plugins.neavo_image_generate.plugin import (
    MAX_PROMPT_LENGTH,
    PRIORITY,
    REVERSE_COMMAND_TOKEN,
    NeavoImageGeneratePlugin,
    extract_command,
    extract_prompt,
)
from tests.config_helpers import (
    FakeConfigManager,
    build_plugin_snapshot,
    plugin_config_view,
)

API_TOKEN = "test-neavo-token"
BASE_URL = "https://neavo.example"
ALLOWED_GROUP_ID = "40000"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
GIF_BYTES = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)
JOB_A = UUID("550e8400-e29b-41d4-a716-446655440000")
JOB_B = UUID("550e8400-e29b-41d4-a716-446655440001")

type AsyncHttpHandler = Callable[
    [httpx.Request], Coroutine[None, None, httpx.Response]
]
type SleepFunction = Callable[[float], Awaitable[None]]


def build_config(**overrides: object) -> NeavoImageGenerateConfig:
    """构造带安全假 Token 的测试配置。"""
    values: dict[str, object] = {
        "groups": [ALLOWED_GROUP_ID],
        "base_url": BASE_URL,
        "api_token": API_TOKEN,
        "poll_interval_seconds": 3.0,
        "generation_timeout_seconds": 60.0,
        "request_timeout_seconds": 10.0,
        "max_image_bytes": 20 * 1024 * 1024,
    }
    values.update(overrides)
    return NeavoImageGenerateConfig.model_validate(values)


def build_group_message(
    *,
    text: str = "#生图 一只猫",
    message: list[MessageSegment] | None = None,
    group_id: str = ALLOWED_GROUP_ID,
    user_id: str = "20001",
    message_id: str = "30001",
) -> GroupMessage:
    """构造测试用群消息。"""
    segments: list[MessageSegment]
    if message is not None:
        segments = message
    else:
        segments = [cast(MessageSegment, Text.new(text))]
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id=user_id,
        message_id=message_id,
        group_id=group_id,
        group_name="测试群",
        message=segments,
        raw_message=text,
        sender=Sender(user_id=user_id, nickname=f"用户{user_id}", role="member"),
    )


def to_stored_message(message: GroupMessage) -> StoredGroupMessage:
    """把群事件转成插件引用查询使用的 DTO。"""
    return StoredGroupMessage(
        row_id=1,
        scope=GroupDataScope(
            bot_id=message.self_id,
            group_id=message.group_id,
        ),
        message_id=message.message_id,
        group_name=message.group_name,
        sender_id=message.user_id,
        sender_name=message.sender.card or message.sender.nickname,
        sender_role=message.sender.role,
        occurred_at=datetime.fromtimestamp(message.time, tz=timezone.utc),
        direction=(
            "outgoing" if message.post_type == "message_sent" else "incoming"
        ),
        segments=tuple(message.message),
        images=(),
    )


@dataclass(slots=True)
class SentMessage:
    """FakeBot 记录的一次群消息发送。"""

    group_id: str
    segments: list[MessageSegment]


class FakeBot:
    """记录插件发送内容的测试 Bot。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.boot_id = "10000"
        self.sent_messages: list[SentMessage] = []

    async def send_msg(
        self,
        *,
        group_id: str,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """记录发送动作并返回成功响应。"""
        self.sent_messages.append(
            SentMessage(group_id=group_id, segments=list(message_segment or []))
        )
        return Response(status="ok", retcode=0)


class FakeDatabase:
    """按消息 ID 返回测试群消息。"""

    def __init__(self, *, messages: dict[str, GroupMessage] | None = None) -> None:
        """保存可被回复引用的消息。"""
        self.messages = {
            message_id: to_stored_message(message)
            for message_id, message in (messages or {}).items()
        }
        self.searches: list[tuple[str, str, str]] = []

    async def get_active(
        self,
        *,
        scope: GroupDataScope,
        message_id: str,
    ) -> StoredGroupMessage | None:
        """记录查询并返回对应历史消息。"""
        self.searches.append((scope.bot_id, scope.group_id, message_id))
        return self.messages.get(message_id)


class FakeContext:
    """仅提供 Neavo 插件需要的运行期依赖。"""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        stored_messages: dict[str, GroupMessage] | None = None,
    ) -> None:
        """绑定 FakeBot 与测试 HTTP 客户端。"""
        self.bot = FakeBot()
        self.group_messages = FakeDatabase(messages=stored_messages)
        self.direct_httpx = http_client


async def no_sleep(_seconds: float) -> None:
    """跳过真实轮询等待。"""


def extract_at_user(segments: list[MessageSegment]) -> str | None:
    """提取发送消息中的首个艾特目标。"""
    for segment in segments:
        if isinstance(segment, At):
            return segment.data.qq
    return None


def extract_text(segments: list[MessageSegment]) -> str:
    """拼接发送消息中的文本段。"""
    return "".join(
        segment.data.text for segment in segments if isinstance(segment, Text)
    )


def extract_image_bytes(segments: list[MessageSegment]) -> bytes | None:
    """解码发送消息中的首张 base64 图片。"""
    for segment in segments:
        if not isinstance(segment, Image):
            continue
        prefix = "base64://"
        if not segment.data.file.startswith(prefix):
            return None
        return base64.b64decode(segment.data.file.removeprefix(prefix))
    return None


class NeavoImageGenerateConfigTest(unittest.IsolatedAsyncioTestCase):
    """验证 Neavo 插件配置边界。"""

    async def test_config_normalizes_ids_url_and_masks_token(self) -> None:
        """群号、根地址和密钥按配置契约规范化。"""
        config = build_config(
            groups=[40000],
            base_url=" https://neavo.example/ ",
            api_token=f" {API_TOKEN} ",
        )

        self.assertEqual(config.groups, (ALLOWED_GROUP_ID,))
        self.assertEqual(config.base_url, BASE_URL)
        self.assertIsNotNone(config.api_token)
        assert config.api_token is not None
        self.assertEqual(config.api_token.get_secret_value(), API_TOKEN)
        self.assertNotIn(API_TOKEN, repr(config))

    async def test_config_rejects_unknown_and_invalid_values(self) -> None:
        """未知字段、无效 URL 和越界轮询间隔必须显式失败。"""
        invalid_overrides = [
            {"unexpected": True},
            {"base_url": "ftp://neavo.example"},
            {"base_url": "https://neavo.example/path?query=1"},
            {"poll_interval_seconds": 1.9},
            {"poll_interval_seconds": 5.1},
        ]

        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    _ = build_config(**overrides)

    async def test_config_allows_no_token_and_basic_auth_url(self) -> None:
        """无鉴权服务可省略 Token，Basic Auth 也可放在 URL 中。"""
        no_token = build_config(api_token="")
        basic_auth = build_config(
            base_url="https://user:password@neavo.example/"
        )

        self.assertIsNone(no_token.api_token)
        self.assertEqual(
            basic_auth.base_url,
            "https://user:password@neavo.example",
        )


class NeavoImageClientTest(unittest.IsolatedAsyncioTestCase):
    """验证 Neavo HTTP 协议客户端。"""

    async def asyncSetUp(self) -> None:
        """初始化待关闭的测试 HTTP 客户端列表。"""
        self.http_clients: list[httpx.AsyncClient] = []

    async def asyncTearDown(self) -> None:
        """关闭每个 MockTransport 客户端。"""
        for http_client in self.http_clients:
            await http_client.aclose()

    def make_client(
        self,
        *,
        handler: AsyncHttpHandler,
        config: NeavoImageGenerateConfig | None = None,
        sleep: SleepFunction = no_sleep,
    ) -> NeavoImageClient:
        """创建使用 MockTransport 的协议客户端。"""
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.http_clients.append(http_client)
        return NeavoImageClient(
            config=config or build_config(),
            http_client=http_client,
            sleep=sleep,
        )

    async def test_unauthenticated_service_omits_authorization_header(self) -> None:
        """未配置 Token 时请求中不发送空的 Authorization。"""
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertNotIn("Authorization", request.headers)
            return httpx.Response(202, json={"id": str(JOB_A)})

        client = self.make_client(
            handler=handler,
            config=build_config(api_token=""),
        )

        job_id = await client.submit_text_to_image(prompt="无鉴权服务")

        self.assertEqual(job_id, JOB_A)

    async def test_generate_uses_new_routes_and_polls_202_until_image(self) -> None:
        """新版文生图任务按固定间隔轮询，HTTP 202 表示处理中。"""
        requests: list[tuple[str, str]] = []
        sleeps: list[float] = []
        poll_count = 0

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            requests.append((request.method, request.url.path))
            self.assertEqual(request.headers["Authorization"], f"Bearer {API_TOKEN}")
            if request.method == "POST":
                payload = cast(dict[str, object], json.loads(request.content))
                self.assertEqual(payload, {"instruction": "一只戴耳机的橘猫"})
                return httpx.Response(202, json={"id": str(JOB_A)})
            poll_count += 1
            if poll_count <= 2:
                return httpx.Response(202)
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=PNG_BYTES,
            )

        client = self.make_client(handler=handler, sleep=record_sleep)

        result = await client.generate("一只戴耳机的橘猫")

        self.assertEqual(result.job_id, JOB_A)
        self.assertEqual(result.image_bytes, PNG_BYTES)
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual(sleeps, [3.0, 3.0, 3.0])
        self.assertEqual(
            requests,
            [
                ("POST", "/text_to_image"),
                ("GET", f"/text_to_image/{JOB_A}"),
                ("GET", f"/text_to_image/{JOB_A}"),
                ("GET", f"/text_to_image/{JOB_A}"),
            ],
        )

    async def test_describe_uploads_raw_image_and_polls_text_result(self) -> None:
        """反推以原始图片提交，并从 JSON 完成响应读取 Florence-2 文本。"""
        requests: list[tuple[str, str]] = []
        poll_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            requests.append((request.method, request.url.path))
            self.assertEqual(request.headers["Authorization"], f"Bearer {API_TOKEN}")
            if request.method == "POST":
                self.assertEqual(request.url.path, "/image_to_text")
                self.assertEqual(request.headers["Content-Type"], "image/png")
                self.assertEqual(request.content, PNG_BYTES)
                return httpx.Response(202, json={"id": str(JOB_B)})
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(202)
            return httpx.Response(
                200,
                json={"text": "一只白猫\n\nwhite cat, portrait"},
            )

        client = self.make_client(handler=handler)

        result = await client.describe(
            image_bytes=PNG_BYTES,
            mime_type="image/png",
        )

        self.assertEqual(result.job_id, JOB_B)
        self.assertEqual(result.text, "一只白猫\n\nwhite cat, portrait")
        self.assertEqual(
            requests,
            [
                ("POST", "/image_to_text"),
                ("GET", f"/image_to_text/{JOB_B}"),
                ("GET", f"/image_to_text/{JOB_B}"),
            ],
        )

    async def test_submit_rejects_invalid_uuid(self) -> None:
        """202 响应缺少规范 UUID 时作为协议错误失败。"""

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(202, json={"id": "not-a-uuid"})

        client = self.make_client(handler=handler)

        with self.assertRaises(NeavoProtocolError) as raised:
            _ = await client.generate("测试提示词")

        self.assertEqual(raised.exception.stage, "submit")
        self.assertEqual(raised.exception.status_code, 202)

    async def test_submit_transport_error_is_not_retried(self) -> None:
        """POST 网络状态不明时禁止自动重试，避免重复生图。"""
        submit_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal submit_count
            submit_count += 1
            raise httpx.ReadTimeout("提交超时", request=request)

        client = self.make_client(handler=handler)

        with self.assertRaises(NeavoTransportError) as raised:
            _ = await client.generate("测试提示词")

        self.assertEqual(submit_count, 1)
        self.assertEqual(raised.exception.stage, "submit")
        self.assertIsNone(raised.exception.job_id)

    async def test_poll_retries_three_times_after_initial_network_error(self) -> None:
        """GET 连续网络故障最多重试三次后终止。"""
        poll_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            if request.method == "POST":
                return httpx.Response(202, json={"id": str(JOB_A)})
            poll_count += 1
            raise httpx.ConnectError("轮询连接失败", request=request)

        client = self.make_client(handler=handler)

        with self.assertRaises(NeavoTransportError) as raised:
            _ = await client.generate("测试提示词")

        self.assertEqual(poll_count, 1 + MAX_CONSECUTIVE_POLL_RETRIES)
        self.assertEqual(raised.exception.stage, "poll")
        self.assertEqual(raised.exception.job_id, JOB_A)

    async def test_terminal_http_statuses_are_not_polled_again(self) -> None:
        """除 202 外的错误状态均为终态错误。"""
        for status_code in (401, 404, 422, 500, 502, 503):
            with self.subTest(status_code=status_code):
                poll_count = 0

                async def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal poll_count
                    if request.method == "POST":
                        return httpx.Response(202, json={"id": str(JOB_A)})
                    poll_count += 1
                    return httpx.Response(status_code, json={"detail": "secret"})

                client = self.make_client(handler=handler)
                with self.assertRaises(NeavoUpstreamError) as raised:
                    _ = await client.generate("测试提示词")

                self.assertEqual(poll_count, 1)
                self.assertEqual(raised.exception.stage, "poll")
                self.assertEqual(raised.exception.status_code, status_code)
                self.assertEqual(raised.exception.job_id, JOB_A)

    async def test_image_response_validation_rejects_invalid_content(self) -> None:
        """空数据、非图片、不可识别图片和超限图片均被拒绝。"""
        cases: list[tuple[str, dict[str, str], bytes, int]] = [
            ("non-image-type", {"Content-Type": "application/json"}, PNG_BYTES, 1024),
            ("empty", {"Content-Type": "image/png"}, b"", 1024),
            ("unknown", {"Content-Type": "image/png"}, b"not-an-image", 1024),
            (
                "oversized",
                {"Content-Type": "image/png"},
                PNG_BYTES,
                len(PNG_BYTES) - 1,
            ),
        ]
        for name, headers, content, max_image_bytes in cases:
            with self.subTest(name=name):

                async def handler(request: httpx.Request) -> httpx.Response:
                    if request.method == "POST":
                        return httpx.Response(202, json={"id": str(JOB_A)})
                    return httpx.Response(200, headers=headers, content=content)

                client = self.make_client(
                    handler=handler,
                    config=build_config(max_image_bytes=max_image_bytes),
                )
                with self.assertRaises(NeavoProtocolError) as raised:
                    _ = await client.generate("测试提示词")

                self.assertEqual(raised.exception.stage, "validate")
                self.assertEqual(raised.exception.status_code, 200)
                self.assertEqual(raised.exception.job_id, JOB_A)

    async def test_describe_rejects_invalid_input_before_http(self) -> None:
        """反推在发请求前拒绝空图片、不匹配类型和超过 10 MiB 的图片。"""
        request_count = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(500)

        client = self.make_client(handler=handler)
        cases = [
            (b"", "image/png"),
            (PNG_BYTES, "image/jpeg"),
            (b"\x89PNG\r\n\x1a\n" + b"x" * MAX_INPUT_IMAGE_BYTES, "image/png"),
        ]
        for image_bytes, mime_type in cases:
            with self.subTest(mime_type=mime_type, size=len(image_bytes)):
                with self.assertRaises(NeavoProtocolError) as raised:
                    _ = await client.describe(
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                    )
                self.assertEqual(raised.exception.stage, "validate")
        self.assertEqual(request_count, 0)

    async def test_describe_rejects_missing_text_result(self) -> None:
        """反推完成响应缺少非空 text 时作为协议错误失败。"""

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={"id": str(JOB_A)})
            return httpx.Response(200, json={"text": "   "})

        client = self.make_client(handler=handler)

        with self.assertRaises(NeavoProtocolError) as raised:
            _ = await client.describe(
                image_bytes=PNG_BYTES,
                mime_type="image/png",
            )

        self.assertEqual(raised.exception.stage, "validate")
        self.assertEqual(raised.exception.status_code, 200)
        self.assertEqual(raised.exception.job_id, JOB_A)

    async def test_generation_timeout_preserves_job_id(self) -> None:
        """总期限到达时报告超时，并保留已提交任务 ID。"""

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(202, json={"id": str(JOB_A)})

        async def blocking_sleep(_seconds: float) -> None:
            await asyncio.Event().wait()

        client = self.make_client(
            handler=handler,
            config=build_config(generation_timeout_seconds=0.01),
            sleep=blocking_sleep,
        )

        with self.assertRaises(NeavoGenerationTimeoutError) as raised:
            _ = await client.generate("测试提示词")

        self.assertEqual(raised.exception.stage, "poll")
        self.assertEqual(raised.exception.job_id, JOB_A)


class NeavoImageGeneratePluginTest(unittest.IsolatedAsyncioTestCase):
    """验证群聊路由、发送归属与队列并发。"""

    async def asyncSetUp(self) -> None:
        """初始化需在测试结束时清理的插件与客户端。"""
        self.plugins: list[NeavoImageGeneratePlugin] = []
        self.http_clients: list[httpx.AsyncClient] = []
        self.config_managers: list[FakeConfigManager] = []

    async def asyncTearDown(self) -> None:
        """停止消费者并关闭 MockTransport 客户端。"""
        for plugin in self.plugins:
            await plugin.stop_consumers()
        for http_client in self.http_clients:
            await http_client.aclose()

    def make_plugin(
        self,
        *,
        handler: AsyncHttpHandler,
        config: NeavoImageGenerateConfig | None = None,
        sleep: SleepFunction = no_sleep,
        stored_messages: dict[str, GroupMessage] | None = None,
    ) -> tuple[NeavoImageGeneratePlugin, FakeBot]:
        """构造使用真实 BasePlugin 队列和 MockTransport 的插件。"""
        plugin_config = config or build_config()
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.http_clients.append(http_client)
        fake_context = FakeContext(
            http_client=http_client,
            stored_messages=stored_messages,
        )
        manager = FakeConfigManager(
            build_plugin_snapshot(neavo_image_generate=plugin_config)
        )
        self.config_managers.append(manager)
        plugin = NeavoImageGeneratePlugin(
            context=cast(Context, fake_context),
            plugin_config=plugin_config_view(
                manager,
                plugin_id="neavo_image_generate",
            ),
        )
        runtime = plugin._current_runtime()  # pyright: ignore[reportPrivateUsage]
        if runtime is None:
            raise AssertionError("Neavo 测试配置应启用插件")
        runtime.client._sleep = sleep  # pyright: ignore[reportPrivateUsage]
        self.plugins.append(plugin)
        return plugin, fake_context.bot

    async def test_extract_prompt_requires_independent_command_token(self) -> None:
        """命令可跨文本段拼接，但近似词和正文中的令牌不触发。"""
        split_message = build_group_message(
            message=[Text.new("#生"), At.new("99999"), Text.new("图  白猫  ")]
        )
        cases = [
            (split_message, "白猫"),
            (build_group_message(text="#生图\n夜景"), "夜景"),
            (build_group_message(text="#生图"), ""),
            (build_group_message(text="#生图片"), None),
            (build_group_message(text="请用 #生图 画猫"), None),
            (build_group_message(text="普通消息"), None),
        ]

        for message, expected in cases:
            with self.subTest(raw_message=message.raw_message):
                self.assertEqual(extract_prompt(message), expected)

    async def test_reverse_command_is_exact_and_plugin_has_highest_priority(
        self,
    ) -> None:
        """只有独立 #反推 被识别，且插件优先于其他群聊回复插件。"""
        reverse = extract_command(build_group_message(text=REVERSE_COMMAND_TOKEN))

        self.assertIsNotNone(reverse)
        self.assertEqual(reverse.operation if reverse is not None else None, "image_to_text")
        self.assertIsNone(extract_command(build_group_message(text="#反推一下")))
        self.assertIsNone(extract_command(build_group_message(text="请帮我 #反推")))
        self.assertEqual(PRIORITY, 100)

    async def test_queue_filters_other_groups_and_non_commands_before_http(self) -> None:
        """白名单外消息和普通消息不会进入耗时生成流程。"""
        request_count = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(500)

        plugin, bot = self.make_plugin(handler=handler)

        results = await asyncio.gather(
            plugin.add_to_queue(
                build_group_message(group_id="40001", text="#生图 白猫")
            ),
            plugin.add_to_queue(build_group_message(text="#生图片 白猫")),
            plugin.add_to_queue(build_group_message(text="#反推一下")),
            plugin.add_to_queue(build_group_message(text="普通消息")),
        )

        self.assertEqual(results, [False, False, False, False])
        self.assertEqual(request_count, 0)
        self.assertEqual(bot.sent_messages, [])

    async def test_queue_filter_uses_latest_group_config(self) -> None:
        """新增群立即接收命令，移除的群不再进入消费者队列。"""

        async def handler(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("空提示词应在本地返回用法，不应请求上游")

        plugin, _bot = self.make_plugin(handler=handler)
        manager = self.config_managers[-1]
        self.assertFalse(
            await plugin.add_to_queue(
                build_group_message(text="#生图", group_id="50000")
            )
        )

        manager.plugins = build_plugin_snapshot(
            revision=2,
            neavo_image_generate=build_config(groups=["50000"]),
        )

        self.assertFalse(
            await plugin.add_to_queue(
                build_group_message(text="#生图", group_id="40000")
            )
        )
        self.assertTrue(
            await plugin.add_to_queue(
                build_group_message(text="#生图", group_id="50000")
            )
        )

    async def test_empty_and_oversized_prompts_are_rejected_locally(self) -> None:
        """空提示词和 4097 字提示词只向发起者返回本地校验错误。"""
        request_count = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(500)

        plugin, bot = self.make_plugin(handler=handler)
        empty_message = build_group_message(text="#生图", user_id="21001")
        long_message = build_group_message(
            text=f"#生图 {'猫' * (MAX_PROMPT_LENGTH + 1)}",
            user_id="21002",
            message_id="30002",
        )

        results = await asyncio.gather(
            plugin.add_to_queue(empty_message),
            plugin.add_to_queue(long_message),
        )

        self.assertEqual(results, [True, True])
        self.assertEqual(request_count, 0)
        self.assertEqual(
            {extract_at_user(item.segments) for item in bot.sent_messages},
            {"21001", "21002"},
        )
        sent_text = "\n".join(extract_text(item.segments) for item in bot.sent_messages)
        self.assertIn("填写图片描述", sent_text)
        self.assertIn(str(MAX_PROMPT_LENGTH), sent_text)

    async def test_reverse_without_image_returns_usage_without_http(self) -> None:
        """#反推 没有当前或回复图片时只返回明确用法。"""
        request_count = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(500)

        plugin, bot = self.make_plugin(handler=handler)

        handled = await plugin.add_to_queue(
            build_group_message(text="#反推", user_id="21501")
        )

        self.assertTrue(handled)
        self.assertEqual(request_count, 0)
        self.assertEqual(len(bot.sent_messages), 1)
        self.assertEqual(extract_at_user(bot.sent_messages[0].segments), "21501")
        self.assertIn("携带一张图片", extract_text(bot.sent_messages[0].segments))

    async def test_reverse_accepts_attached_image(self) -> None:
        """#反推 可直接携带图片，并把反推文本艾特回发起者。"""
        requests: list[tuple[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.host == "media.example":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    content=PNG_BYTES,
                )
            if request.method == "POST":
                self.assertEqual(request.url.path, "/image_to_text")
                self.assertEqual(request.headers["Content-Type"], "image/png")
                self.assertEqual(request.content, PNG_BYTES)
                return httpx.Response(202, json={"id": str(JOB_A)})
            return httpx.Response(
                200,
                json={"text": "白猫坐在窗边\n\nwhite cat, window"},
            )

        plugin, bot = self.make_plugin(handler=handler)
        message = build_group_message(
            message=[
                cast(MessageSegment, Text.new("#反推")),
                cast(
                    MessageSegment,
                    Image.new(
                        "attached.png",
                        url="https://media.example/attached.png",
                    ),
                ),
            ],
            user_id="21601",
        )

        handled = await plugin.add_to_queue(message)

        self.assertTrue(handled)
        self.assertEqual(
            requests,
            [
                ("GET", "/attached.png"),
                ("POST", "/image_to_text"),
                ("GET", f"/image_to_text/{JOB_A}"),
            ],
        )
        self.assertEqual(len(bot.sent_messages), 2)
        self.assertIn("正在反推", extract_text(bot.sent_messages[0].segments))
        self.assertEqual(extract_at_user(bot.sent_messages[1].segments), "21601")
        self.assertIn("白猫坐在窗边", extract_text(bot.sent_messages[1].segments))

    async def test_reverse_accepts_replied_image(self) -> None:
        """回复一条含图片的消息后发送 #反推 也能读取被回复图片。"""
        replied_message = build_group_message(
            message=[
                cast(
                    MessageSegment,
                    Image.new(
                        "replied.webp",
                        url="https://media.example/replied.webp",
                    ),
                )
            ],
            user_id="21602",
            message_id="31602",
        )
        requests: list[tuple[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.host == "media.example":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    content=PNG_BYTES,
                )
            if request.method == "POST":
                return httpx.Response(202, json={"id": str(JOB_B)})
            return httpx.Response(200, json={"text": "回复图片的描述"})

        plugin, bot = self.make_plugin(
            handler=handler,
            stored_messages={"31602": replied_message},
        )
        command_message = build_group_message(
            message=[
                cast(MessageSegment, Reply.new("31602")),
                cast(MessageSegment, Text.new("#反推")),
            ],
            user_id="21603",
            message_id="31603",
        )

        handled = await plugin.add_to_queue(command_message)

        self.assertTrue(handled)
        self.assertEqual(
            requests,
            [
                ("GET", "/replied.webp"),
                ("POST", "/image_to_text"),
                ("GET", f"/image_to_text/{JOB_B}"),
            ],
        )
        self.assertEqual(extract_at_user(bot.sent_messages[-1].segments), "21603")
        self.assertIn("回复图片的描述", extract_text(bot.sent_messages[-1].segments))

    async def test_max_length_prompt_generates_and_sends_base64_image(self) -> None:
        """4096 字边界提示词可以生成，并把图片艾特回原用户。"""
        prompt = "猫" * MAX_PROMPT_LENGTH
        submitted_prompts: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                payload = cast(dict[str, object], json.loads(request.content))
                instruction = payload.get("instruction")
                self.assertIsInstance(instruction, str)
                submitted_prompts.append(cast(str, instruction))
                return httpx.Response(202, json={"id": str(JOB_A)})
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=PNG_BYTES,
            )

        plugin, bot = self.make_plugin(handler=handler)

        handled = await plugin.add_to_queue(
            build_group_message(text=f"#生图 {prompt}", user_id="22001")
        )

        self.assertTrue(handled)
        self.assertEqual(submitted_prompts, [prompt])
        self.assertEqual(len(bot.sent_messages), 2)
        status, result = bot.sent_messages
        self.assertEqual(extract_at_user(status.segments), "22001")
        self.assertIn("正在生成", extract_text(status.segments))
        self.assertEqual(extract_at_user(result.segments), "22001")
        self.assertEqual(extract_image_bytes(result.segments), PNG_BYTES)

    async def test_two_users_can_finish_in_reverse_order_without_result_mixup(
        self,
    ) -> None:
        """多人请求各自绑定任务 ID，后提交者可先完成且图片不串人。"""
        slow_poll_started = asyncio.Event()
        allow_slow_result = asyncio.Event()
        prompt_jobs = {"慢任务": JOB_A, "快任务": JOB_B}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                payload = cast(dict[str, object], json.loads(request.content))
                prompt = payload.get("instruction")
                self.assertIsInstance(prompt, str)
                return httpx.Response(
                    202,
                    json={"id": str(prompt_jobs[cast(str, prompt)])},
                )
            if request.url.path.endswith(str(JOB_A)):
                slow_poll_started.set()
                await allow_slow_result.wait()
                return httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    content=PNG_BYTES,
                )
            return httpx.Response(
                200,
                headers={"Content-Type": "image/gif"},
                content=GIF_BYTES,
            )

        plugin, bot = self.make_plugin(handler=handler)
        slow_task = asyncio.create_task(
            plugin.add_to_queue(
                build_group_message(
                    text="#生图 慢任务", user_id="23001", message_id="33001"
                )
            )
        )
        await asyncio.wait_for(slow_poll_started.wait(), timeout=1)
        fast_task = asyncio.create_task(
            plugin.add_to_queue(
                build_group_message(
                    text="#生图 快任务", user_id="23002", message_id="33002"
                )
            )
        )
        try:
            self.assertTrue(await asyncio.wait_for(fast_task, timeout=1))
            self.assertFalse(slow_task.done())
        finally:
            allow_slow_result.set()
        self.assertTrue(await asyncio.wait_for(slow_task, timeout=1))

        result_by_user = {
            cast(str, extract_at_user(item.segments)): image_bytes
            for item in bot.sent_messages
            if (image_bytes := extract_image_bytes(item.segments)) is not None
        }
        self.assertEqual(
            result_by_user,
            {"23001": PNG_BYTES, "23002": GIF_BYTES},
        )
        completion_order = [
            extract_at_user(item.segments)
            for item in bot.sent_messages
            if extract_image_bytes(item.segments) is not None
        ]
        self.assertEqual(completion_order, ["23002", "23001"])

    async def test_sixth_request_waits_until_one_of_five_consumers_is_free(
        self,
    ) -> None:
        """并发峰值固定为五，第六个任务在插件队列中等待。"""
        release_results = asyncio.Event()
        five_active = asyncio.Event()
        active_polls = 0
        max_active_polls = 0
        submitted_prompts: list[str] = []
        job_ids = [
            UUID(f"550e8400-e29b-41d4-a716-{index:012d}") for index in range(1, 7)
        ]
        prompt_jobs = {f"任务{index}": job_ids[index - 1] for index in range(1, 7)}

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_polls, max_active_polls
            if request.method == "POST":
                payload = cast(dict[str, object], json.loads(request.content))
                prompt = payload.get("instruction")
                self.assertIsInstance(prompt, str)
                prompt_text = cast(str, prompt)
                submitted_prompts.append(prompt_text)
                return httpx.Response(202, json={"id": str(prompt_jobs[prompt_text])})
            active_polls += 1
            max_active_polls = max(max_active_polls, active_polls)
            if active_polls == 5:
                five_active.set()
            try:
                await release_results.wait()
            finally:
                active_polls -= 1
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=PNG_BYTES,
            )

        plugin, _bot = self.make_plugin(handler=handler)
        tasks = [
            asyncio.create_task(
                plugin.add_to_queue(
                    build_group_message(
                        text=f"#生图 任务{index}",
                        user_id=f"24{index:03d}",
                        message_id=f"34{index:03d}",
                    )
                )
            )
            for index in range(1, 7)
        ]
        try:
            await asyncio.wait_for(five_active.wait(), timeout=1)
            self.assertEqual(len(submitted_prompts), 5)
            self.assertFalse(tasks[5].done())
        finally:
            release_results.set()

        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        self.assertEqual(results, [True] * 6)
        self.assertEqual(max_active_polls, 5)
        self.assertCountEqual(submitted_prompts, [f"任务{index}" for index in range(1, 7)])

    async def test_failed_request_does_not_cancel_another_user_request(self) -> None:
        """单个上游失败只通知对应用户，不影响并发中的成功任务。"""
        prompt_jobs = {"失败任务": JOB_A, "成功任务": JOB_B}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                payload = cast(dict[str, object], json.loads(request.content))
                prompt = payload.get("instruction")
                self.assertIsInstance(prompt, str)
                return httpx.Response(
                    202,
                    json={"id": str(prompt_jobs[cast(str, prompt)])},
                )
            if request.url.path.endswith(str(JOB_A)):
                return httpx.Response(500, json={"detail": API_TOKEN})
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=PNG_BYTES,
            )

        plugin, bot = self.make_plugin(handler=handler)
        results = await asyncio.gather(
            plugin.add_to_queue(
                build_group_message(
                    text="#生图 失败任务", user_id="25001", message_id="35001"
                )
            ),
            plugin.add_to_queue(
                build_group_message(
                    text="#生图 成功任务", user_id="25002", message_id="35002"
                )
            ),
        )

        self.assertEqual(results, [True, True])
        failure_messages = [
            item
            for item in bot.sent_messages
            if extract_at_user(item.segments) == "25001"
        ]
        success_messages = [
            item
            for item in bot.sent_messages
            if extract_at_user(item.segments) == "25002"
        ]
        self.assertTrue(
            any("生图失败" in extract_text(item.segments) for item in failure_messages)
        )
        self.assertTrue(
            any(extract_image_bytes(item.segments) == PNG_BYTES for item in success_messages)
        )
        all_sent_text = "\n".join(
            extract_text(item.segments) for item in bot.sent_messages
        )
        self.assertNotIn(API_TOKEN, all_sent_text)


if __name__ == "__main__":
    unittest.main()
