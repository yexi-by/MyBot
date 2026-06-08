"""NapCat 群聊本地工具测试。"""

import unittest
from typing import cast

import httpx

from app.models import (
    At,
    File,
    Forward,
    GroupMessage,
    JsonObject,
    JsonValue,
    Image,
    MessageSegment,
    NapCatId,
    Response,
    Sender,
    Share,
    Text,
)
from app.services.napcat.group_tools import (
    GetForwardMessageImagesArgs,
    GetGroupHistoryMessagesArgs,
    NapCatGroupToolExecutor,
    NapCatGroupToolBot,
)
from app.services.napcat.group_tools.protocols import CachedNapCatMessage


def build_group_message(
    *,
    text: str = "hello",
    message: list[MessageSegment] | None = None,
    raw_message: str | None = None,
) -> GroupMessage:
    """构造测试用群消息。"""
    message_segments: list[MessageSegment]
    if message is None:
        message_segments = [Text.new(text)]
    else:
        message_segments = message
    raw_message_text = raw_message
    if raw_message_text is None:
        raw_message_text = text
    return GroupMessage(
        time=1_777_132_900,
        self_id="10000",
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id="20000",
        message_id="30000",
        group_id="40000",
        group_name="测试群",
        message=message_segments,
        raw_message=raw_message_text,
        sender=Sender(user_id="20000", nickname="夜袭", role="member"),
    )


IMAGE_A_BASE64 = "aW1hZ2UtYQ=="
IMAGE_B_BASE64 = "aW1hZ2UtYg=="


class FakeBot:
    """测试用 NapCat Bot。"""

    def __init__(
        self,
        forward_responses: dict[NapCatId, Response] | None = None,
        image_responses: dict[str, Response] | None = None,
    ) -> None:
        """初始化发送记录。"""
        self.sent_count = 0
        self.forward_responses = forward_responses or {}
        self.forward_calls: list[NapCatId] = []
        self.image_responses = image_responses or {}
        self.image_calls: list[tuple[str | None, str | None]] = []

    async def send_msg(
        self,
        *,
        group_id: NapCatId,
        message_segment: list[MessageSegment] | None = None,
    ) -> Response:
        """记录发送动作并返回成功响应。"""
        _ = (group_id, message_segment)
        self.sent_count += 1
        return Response(status="ok", retcode=0)

    async def get_group_root_files(
        self, group_id: NapCatId, file_count: int = 50
    ) -> Response:
        """返回空群文件列表。"""
        _ = (group_id, file_count)
        return Response(status="ok", retcode=0, data=[])

    async def get_group_files_by_folder(
        self,
        group_id: NapCatId,
        folder_id: str | None = None,
        folder: str | None = None,
        file_count: int = 50,
    ) -> Response:
        """返回空文件夹列表。"""
        _ = (group_id, folder_id, folder, file_count)
        return Response(status="ok", retcode=0, data=[])

    async def get_group_file_url(self, group_id: NapCatId, file_id: str) -> Response:
        """返回测试下载链接。"""
        _ = (group_id, file_id)
        return Response(status="ok", retcode=0, data={"url": "https://example.com/a"})

    async def get_forward_msg(self, message_id: NapCatId) -> Response:
        """返回预置合并转发详情。"""
        self.forward_calls.append(message_id)
        response = self.forward_responses.get(message_id)
        if response is not None:
            return response
        return Response(status="failed", retcode=404, message="合并转发不存在")

    async def get_image(
        self, file_id: str | None = None, file: str | None = None
    ) -> Response:
        """返回预置图片信息。"""
        self.image_calls.append((file_id, file))
        key = file_id if file_id is not None else file
        if key is not None and key in self.image_responses:
            return self.image_responses[key]
        return Response(status="failed", retcode=404, message="图片不存在")


class FakeDatabase:
    """测试用 Redis 历史消息数据库。"""

    async def search_messages(
        self,
        *,
        self_id: str,
        message_id: str | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: str | None = None,
        user_id: str | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> CachedNapCatMessage | list[CachedNapCatMessage] | None:
        """返回空历史。"""
        _ = (
            self_id,
            message_id,
            root,
            limit_tuple,
            group_id,
            user_id,
            max_time,
            min_time,
        )
        return None


class HistoryDatabase:
    """返回指定群消息列表的测试数据库。"""

    def __init__(self, messages: list[CachedNapCatMessage]) -> None:
        """保存待返回的历史消息。"""
        self.messages: list[CachedNapCatMessage] = messages

    async def search_messages(
        self,
        *,
        self_id: str,
        message_id: str | None = None,
        root: str | None = None,
        limit_tuple: tuple[int, int] | None = None,
        group_id: str | None = None,
        user_id: str | None = None,
        max_time: int | None = None,
        min_time: int | None = None,
    ) -> CachedNapCatMessage | list[CachedNapCatMessage] | None:
        """返回预置历史消息。"""
        _ = (
            self_id,
            message_id,
            root,
            limit_tuple,
            group_id,
            user_id,
            max_time,
            min_time,
        )
        return self.messages


class NapCatGroupToolExecutorTest(unittest.IsolatedAsyncioTestCase):
    """验证 NapCat 本地工具行为。"""

    async def test_content_directives_build_reply_at_and_spaced_text(self) -> None:
        """content 标记会修饰最终消息，并在正文前补空格。"""
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        segments = executor.build_message_segments_from_content(
            "<Reply>\n<At>20000</At>\n测试通过"
        )

        self.assertEqual([segment.type for segment in segments], ["reply", "at", "text"])
        text_segment = segments[-1]
        self.assertIsInstance(text_segment, Text)
        text_segment = cast(Text, text_segment)
        self.assertEqual(text_segment.data.text, " 测试通过")

    async def test_mention_all_disabled_raises_model_visible_error(self) -> None:
        """关闭 @全体 时 content 标记解析会返回模型可读错误。"""
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=FakeDatabase(),
            event=build_group_message(),
            allow_mention_all=False,
        )

        with self.assertRaisesRegex(ValueError, "@全体"):
            _ = executor.build_message_segments_from_content("<At>all</At>\n测试")

    async def test_executor_does_not_expose_message_modifier_tools(self) -> None:
        """本地群工具集仅暴露信息工具，消息修饰由 content 标记承担。"""
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        tool_names = {tool.name for tool in executor.list_tools()}

        self.assertNotIn("qq__mention_user", tool_names)
        self.assertNotIn("qq__reply_current_message", tool_names)
        self.assertNotIn("qq__finish_conversation", tool_names)
        self.assertIn("qq__get_forward_message", tool_names)

    async def test_forward_tool_schema_only_exposes_message_id(self) -> None:
        """合并转发工具只向模型暴露 message_id 参数。"""
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        tool = next(
            item
            for item in executor.list_tools()
            if item.name == "qq__get_forward_message"
        )

        properties = tool.parameters["properties"]
        self.assertIsInstance(properties, dict)
        property_map = cast(JsonObject, properties)
        self.assertEqual(set(property_map.keys()), {"message_id"})

    def test_history_duration_requires_minutes(self) -> None:
        """按分钟查询历史时必须明确分钟数。"""
        with self.assertRaises(ValueError):
            GetGroupHistoryMessagesArgs.model_validate(
                {"query_mode": "recent_duration"}
            )

    def test_forward_image_args_require_indices_by_mode(self) -> None:
        """不同合并转发图片选择模式需要明确的定位参数。"""
        single = GetForwardMessageImagesArgs.model_validate(
            {
                "message_id": "root-forward",
                "mode": "single",
                "message_index": 1,
                "image_index": 1,
            }
        )
        self.assertEqual(single.mode, "single")

        message = GetForwardMessageImagesArgs.model_validate(
            {
                "message_id": "root-forward",
                "mode": "message",
                "message_index": 2,
            }
        )
        self.assertEqual(message.mode, "message")

        all_images = GetForwardMessageImagesArgs.model_validate(
            {"message_id": "root-forward", "mode": "all"}
        )
        self.assertEqual(all_images.mode, "all")

        with self.assertRaises(ValueError):
            _ = GetForwardMessageImagesArgs.model_validate(
                {"message_id": "root-forward", "mode": "single"}
            )

        with self.assertRaises(ValueError):
            _ = GetForwardMessageImagesArgs.model_validate(
                {
                    "message_id": "root-forward",
                    "mode": "all",
                    "message_index": 1,
                }
            )

    async def test_history_messages_include_readable_non_text_segments(self) -> None:
        """历史查询会把艾特、图片、文件和卡片转成模型可读文本。"""
        history_message = build_group_message(
            message=[
                At.new("12345", name="小明"),
                Text.new("看这个"),
                Image.new(
                    "image-cache.jpg",
                    summary="[图片]",
                    url="https://example.com/image.jpg",
                    file_size=4096,
                ),
                File.new(
                    "report.pdf",
                    name="报告.pdf",
                    file_id="file-1",
                    file_size=2048,
                ),
                Share.new(
                    "https://example.com/share",
                    title="链接标题",
                    content="链接描述",
                ),
            ],
            raw_message="@小明 看这个[图片][文件][分享]",
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=HistoryDatabase([history_message]),
            event=build_group_message(),
        )

        result = await executor.call_tool(
            "qq__get_group_history_messages",
            {"query_mode": "recent_count", "limit": 1},
        )

        self.assertIsInstance(result, dict)
        result_object = cast(JsonObject, result)
        messages_value = result_object["messages"]
        self.assertIsInstance(messages_value, list)
        messages = cast(list[JsonValue], messages_value)
        self.assertEqual(len(messages), 1)
        first_message_value = messages[0]
        self.assertIsInstance(first_message_value, dict)
        first_message = cast(JsonObject, first_message_value)
        text_value = first_message["text"]
        self.assertIsInstance(text_value, str)
        text = cast(str, text_value)
        self.assertIn("艾特: 小明 (12345)", text)
        self.assertIn("看这个", text)
        self.assertIn("图片消息", text)
        self.assertIn("未附带图片内容", text)
        self.assertIn("报告.pdf", text)
        self.assertIn("链接标题", text)
        segment_types = first_message["segment_types"]
        self.assertEqual(
            segment_types,
            ["at", "text", "image", "file", "share"],
        )

    async def test_forward_tool_returns_complete_structured_messages(self) -> None:
        """合并转发工具返回完整结构、原始消息段和辅助可读文本。"""
        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "sender": {"nickname": "小明", "user_id": "12345"},
                                "time": 1_777_132_800,
                                "message_id": "forward-msg-1",
                                "message": [
                                    {"type": "text", "data": {"text": "看文件"}},
                                    {
                                        "type": "file",
                                        "data": {
                                            "file": "report.pdf",
                                            "name": "报告.pdf",
                                            "file_id": "file-1",
                                            "file_size": 2048,
                                        },
                                    },
                                ],
                            }
                        ]
                    },
                )
            }
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool(
            "qq__get_forward_message",
            {"message_id": "root-forward"},
        )

        result_object = require_json_object(result)
        self.assertIs(result_object["ok"], True)
        self.assertIs(result_object["complete"], True)
        self.assertIn("看文件", require_string(result_object["readable_text"]))
        messages = require_json_list(result_object["messages"])
        first_message = require_json_object(messages[0])
        self.assertEqual(first_message["segment_types"], ["text", "file"])
        self.assertIn("报告.pdf", require_string(first_message["text"]))
        segments = require_json_list(first_message["segments"])
        self.assertEqual(require_json_object(segments[1])["type"], "file")

    async def test_forward_tool_marks_images_for_followup_fetch(self) -> None:
        """合并转发工具发现图片时，会明确给出图片工具后续读取方式。"""
        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "sender": {"nickname": "小明"},
                                "message": [
                                    {"type": "text", "data": {"text": "看图"}},
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "a.jpg",
                                            "file_id": "img-a",
                                            "summary": "[图片A]",
                                        },
                                    },
                                ],
                            },
                            {
                                "sender": {"nickname": "小红"},
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "b.jpg",
                                            "file_id": "img-b",
                                            "summary": "[图片B]",
                                        },
                                    }
                                ],
                            },
                        ]
                    },
                )
            }
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool(
            "qq__get_forward_message",
            {"message_id": "root-forward"},
        )

        result_object = require_json_object(result)
        self.assertEqual(result_object["image_count"], 2)
        image_access = require_json_object(result_object["image_access"])
        self.assertEqual(image_access["tool_name"], "qq__get_forward_message_images")
        recommended_arguments = require_json_object(
            image_access["recommended_arguments"]
        )
        self.assertEqual(recommended_arguments["message_id"], "root-forward")
        self.assertEqual(recommended_arguments["mode"], "all")
        readable_text = require_string(result_object["readable_text"])
        self.assertIn("qq__get_forward_message_images", readable_text)

    async def test_forward_image_tool_fetches_all_images_with_artifacts(self) -> None:
        """合并转发图片工具按 all 模式批量返回图片附件。"""
        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "sender": {"nickname": "小明"},
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "a.jpg",
                                            "file_id": "img-a",
                                            "summary": "[图片A]",
                                        },
                                    }
                                ],
                            },
                            {
                                "sender": {"nickname": "小红"},
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "b.jpg",
                                            "file_id": "img-b",
                                            "summary": "[图片B]",
                                        },
                                    }
                                ],
                            },
                        ]
                    },
                )
            },
            image_responses={
                "a.jpg": Response(
                    status="ok",
                    retcode=0,
                    data={"file": "a.jpg", "base64": IMAGE_A_BASE64},
                ),
                "b.jpg": Response(
                    status="ok",
                    retcode=0,
                    data={"file": "b.jpg", "base64": IMAGE_B_BASE64},
                ),
            },
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool_with_artifacts(
            "qq__get_forward_message_images",
            {"message_id": "root-forward", "mode": "all"},
        )

        result_object = require_json_object(result.result)
        self.assertIs(result_object["ok"], True)
        self.assertEqual(result_object["action"], "get_forward_message_images")
        self.assertEqual(result_object["total_images"], 2)
        self.assertEqual(result_object["returned_count"], 2)
        self.assertIs(result_object["truncated"], False)
        self.assertEqual(len(result.image_artifacts), 2)
        self.assertEqual(result.image_artifacts[0].image_bytes, b"image-a")
        self.assertEqual(result.image_artifacts[1].image_bytes, b"image-b")
        images = require_json_list(result_object["images"])
        first_image = require_json_object(images[0])
        self.assertEqual(first_image["source"], "napcat_refresh")
        self.assertEqual(bot.image_calls, [(None, "a.jpg"), (None, "b.jpg")])

    async def test_forward_image_tool_prefers_segment_url_download(self) -> None:
        """图片段带 URL 时直接下载，不先调用 NapCat 获取图片。"""
        requested_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            return httpx.Response(200, content=b"url-image")

        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "a.jpg",
                                            "file_id": "img-a",
                                            "url": "https://media.example/a.jpg",
                                        },
                                    }
                                ]
                            }
                        ]
                    },
                )
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            executor = NapCatGroupToolExecutor(
                bot=cast(NapCatGroupToolBot, bot),
                database=FakeDatabase(),
                event=build_group_message(),
                http_client=http_client,
            )

            result = await executor.call_tool_with_artifacts(
                "qq__get_forward_message_images",
                {"message_id": "root-forward", "mode": "all"},
            )

        result_object = require_json_object(result.result)
        self.assertEqual(result_object["returned_count"], 1)
        self.assertEqual(len(result.image_artifacts), 1)
        self.assertEqual(result.image_artifacts[0].image_bytes, b"url-image")
        images = require_json_list(result_object["images"])
        first_image = require_json_object(images[0])
        self.assertEqual(first_image["source"], "direct_url")
        self.assertEqual(requested_urls, ["https://media.example/a.jpg"])
        self.assertEqual(bot.image_calls, [])

    async def test_forward_image_tool_limits_all_mode_by_configuration(self) -> None:
        """all 模式会按配置截断，避免一次读取过多图片。"""
        messages: list[JsonValue] = []
        image_responses: dict[str, Response] = {}
        for index in range(1, 6):
            file_id = f"img-{index}"
            messages.append(
                {
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": f"{file_id}.jpg",
                                "file_id": file_id,
                            },
                        }
                    ]
                }
            )
            image_responses[f"{file_id}.jpg"] = Response(
                status="ok",
                retcode=0,
                data={"file": f"{file_id}.jpg", "base64": IMAGE_A_BASE64},
            )
        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={"messages": messages},
                )
            },
            image_responses=image_responses,
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
            forward_image_max_all_images=3,
        )

        result = await executor.call_tool_with_artifacts(
            "qq__get_forward_message_images",
            {"message_id": "root-forward", "mode": "all"},
        )

        result_object = require_json_object(result.result)
        self.assertEqual(result_object["total_images"], 5)
        self.assertEqual(result_object["returned_count"], 3)
        self.assertIs(result_object["truncated"], True)
        self.assertEqual(len(result.image_artifacts), 3)

    async def test_forward_image_tool_returns_partial_errors(self) -> None:
        """部分图片读取失败时，成功图片仍作为附件返回。"""
        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {"file": "a.jpg", "file_id": "img-a"},
                                    }
                                ]
                            },
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {"file": "missing.jpg", "file_id": "missing"},
                                    }
                                ]
                            },
                        ]
                    },
                )
            },
            image_responses={
                "a.jpg": Response(
                    status="ok",
                    retcode=0,
                    data={"file": "a.jpg", "base64": IMAGE_A_BASE64},
                )
            },
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool_with_artifacts(
            "qq__get_forward_message_images",
            {"message_id": "root-forward", "mode": "all"},
        )

        result_object = require_json_object(result.result)
        self.assertIs(result_object["ok"], True)
        self.assertEqual(result_object["returned_count"], 1)
        self.assertEqual(len(result.image_artifacts), 1)
        errors = require_json_list(result_object["errors"])
        first_error = require_json_object(errors[0])
        self.assertEqual(first_error["error_type"], "NapCatActionFailed")

    async def test_forward_tool_recursively_fetches_nested_forward_id(self) -> None:
        """只有 ID 的嵌套合并转发会继续调用 NapCat 读取。"""
        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "sender": {"nickname": "外层"},
                                "message": [
                                    {"type": "forward", "data": {"id": "nested-forward"}}
                                ],
                            }
                        ]
                    },
                ),
                "nested-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "sender": {"nickname": "内层"},
                                "message": [
                                    {"type": "text", "data": {"text": "嵌套正文"}}
                                ],
                            }
                        ]
                    },
                ),
            }
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool(
            "qq__get_forward_message",
            {"message_id": "root-forward"},
        )

        result_object = require_json_object(result)
        self.assertEqual(bot.forward_calls, ["root-forward", "nested-forward"])
        self.assertIs(result_object["complete"], True)
        messages = require_json_list(result_object["messages"])
        first_message = require_json_object(messages[0])
        nested_forwards = require_json_list(first_message["nested_forwards"])
        nested = require_json_object(nested_forwards[0])
        self.assertEqual(nested["message_id"], "nested-forward")
        self.assertIn("嵌套正文", require_string(nested["readable_text"]))

    async def test_forward_tool_uses_embedded_nested_content_without_fetch(self) -> None:
        """已内嵌的嵌套合并转发内容不会额外请求 NapCat。"""
        bot = FakeBot(
            forward_responses={
                "root-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "forward",
                                        "data": {
                                            "id": "embedded-forward",
                                            "content": [
                                                {
                                                    "sender": {"nickname": "内嵌"},
                                                    "message": [
                                                        {
                                                            "type": "text",
                                                            "data": {"text": "内嵌正文"},
                                                        }
                                                    ],
                                                }
                                            ],
                                        },
                                    }
                                ]
                            }
                        ]
                    },
                )
            }
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool(
            "qq__get_forward_message",
            {"message_id": "root-forward"},
        )

        result_object = require_json_object(result)
        self.assertEqual(bot.forward_calls, ["root-forward"])
        messages = require_json_list(result_object["messages"])
        nested_forwards = require_json_list(
            require_json_object(messages[0])["nested_forwards"]
        )
        nested = require_json_object(nested_forwards[0])
        self.assertEqual(nested["message_id"], "embedded-forward")
        self.assertIn("内嵌正文", require_string(nested["readable_text"]))

    async def test_forward_tool_stops_recursive_forward_cycle(self) -> None:
        """循环嵌套会停止递归并返回不完整标记。"""
        bot = FakeBot(
            forward_responses={
                "loop-forward": Response(
                    status="ok",
                    retcode=0,
                    data={
                        "messages": [
                            {
                                "message": [
                                    {"type": "forward", "data": {"id": "loop-forward"}}
                                ]
                            }
                        ]
                    },
                )
            }
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, bot),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool(
            "qq__get_forward_message",
            {"message_id": "loop-forward"},
        )

        result_object = require_json_object(result)
        self.assertIs(result_object["complete"], False)
        errors = require_json_list(result_object["errors"])
        first_error = require_json_object(errors[0])
        self.assertEqual(first_error["error_type"], "ForwardCycle")

    async def test_forward_tool_returns_model_visible_root_error(self) -> None:
        """根合并转发读取失败时返回结构化可恢复错误。"""
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=FakeDatabase(),
            event=build_group_message(),
        )

        result = await executor.call_tool(
            "qq__get_forward_message",
            {"message_id": "missing-forward"},
        )

        result_object = require_json_object(result)
        self.assertIs(result_object["ok"], False)
        self.assertIs(result_object["is_error"], True)
        self.assertEqual(result_object["error_type"], "NapCatActionFailed")

    async def test_formatter_tells_model_to_call_forward_tool(self) -> None:
        """只有 ID 的合并转发提示模型主动调用工具。"""
        message = build_group_message(
            message=[Forward.new("forward-empty")],
            raw_message="[合并转发]",
        )
        executor = NapCatGroupToolExecutor(
            bot=cast(NapCatGroupToolBot, FakeBot()),
            database=HistoryDatabase([message]),
            event=message,
        )

        result = await executor.call_tool(
            "qq__get_group_history_messages",
            {"query_mode": "recent_count", "limit": 1},
        )

        result_object = require_json_object(result)
        messages = require_json_list(result_object["messages"])
        first_message = require_json_object(messages[0])
        text = require_string(first_message["text"])
        self.assertIn("qq__get_forward_message", text)
        self.assertIn('message_id="forward-empty"', text)


def require_json_object(value: object) -> JsonObject:
    """把测试值收窄为 JSON 对象。"""
    if not isinstance(value, dict):
        raise AssertionError("测试值必须是 JSON 对象")
    return cast(JsonObject, value)


def require_json_list(value: object) -> list[JsonValue]:
    """把测试值收窄为 JSON 数组。"""
    if not isinstance(value, list):
        raise AssertionError("测试值必须是 JSON 数组")
    return cast(list[JsonValue], value)


def require_string(value: object) -> str:
    """把测试值收窄为字符串。"""
    if not isinstance(value, str):
        raise AssertionError("测试值必须是字符串")
    return value
