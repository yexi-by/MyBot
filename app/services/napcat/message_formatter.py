"""NapCat 消息段可读文本格式化器。"""

import json

from pydantic import TypeAdapter, ValidationError

from app.models import (
    At,
    Contact,
    Dice,
    Face,
    File,
    Forward,
    Image,
    JsonObject,
    JsonValue,
    LightApp,
    Location,
    MFace,
    Markdown,
    MessageSegment,
    MiniApp,
    Music,
    Node,
    Poke,
    Record,
    Reply,
    Rps,
    Share,
    Text,
    UnknownSegment,
    Video,
    Xml,
)
from app.models import Json as JsonSegment
from app.models import to_json_value

FIELD_TEXT_LIMIT = 240
JSON_TEXT_LIMIT = 600
MARKDOWN_TEXT_LIMIT = 800
FORWARD_MAX_ITEMS = 8
FORWARD_MAX_DEPTH = 2


class NapCatMessageTextFormatter:
    """把 NapCat 消息段转换为适合模型阅读的中文摘要。"""

    def __init__(self) -> None:
        """初始化消息段解析器。"""
        self.segment_adapter: TypeAdapter[MessageSegment] = TypeAdapter(MessageSegment)
        self.segments_adapter: TypeAdapter[list[MessageSegment]] = TypeAdapter(
            list[MessageSegment]
        )

    def format_segments(
        self,
        *,
        segments: list[MessageSegment],
        images_attached: bool,
        include_at: bool = True,
        include_reply: bool = True,
        include_image_details: bool = True,
    ) -> str:
        """把一组消息段转换为低噪音文本。"""
        text_parts: list[str] = []
        has_image = False
        for segment in segments:
            if isinstance(segment, Image):
                has_image = True
                if include_image_details:
                    text_parts.append(
                        self._format_image_segment(
                            segment=segment,
                            images_attached=images_attached,
                        )
                    )
                continue
            formatted_segment = self._format_segment_text(
                segment=segment,
                depth=0,
                include_at=include_at,
                include_reply=include_reply,
            )
            if formatted_segment is None:
                continue
            cleaned_segment = formatted_segment.strip()
            if cleaned_segment:
                text_parts.append(cleaned_segment)
        text = "\n\n".join(text_parts).strip()
        if has_image and not include_image_details:
            image_notice = "（图片消息，图片已随本条输入提供）"
            if not images_attached:
                image_notice = "（消息包含图片，但当前请求未附带图片内容）"
            if text:
                return f"{text}\n\n{image_notice}"
            return image_notice
        if text:
            return text
        return "（无文本内容）"

    def _format_segment_text(
        self,
        *,
        segment: MessageSegment,
        depth: int,
        include_at: bool,
        include_reply: bool,
    ) -> str | None:
        """把单个非图片消息段转换为可读文本。"""
        if isinstance(segment, Text):
            return segment.data.text
        if isinstance(segment, At):
            if not include_at:
                return None
            return self._format_at_segment(segment=segment)
        if isinstance(segment, Reply):
            if not include_reply:
                return None
            return self._format_reply_segment(segment=segment)
        if isinstance(segment, Share):
            return self._format_share_segment(segment=segment)
        if isinstance(segment, Forward):
            return self._format_forward_segment(segment=segment, depth=depth)
        if isinstance(segment, JsonSegment):
            return self._format_json_payload(label="JSON 卡片", payload=segment.data.data)
        if isinstance(segment, MFace):
            return self._format_mface_segment(segment=segment)
        if isinstance(segment, File):
            return self._format_file_segment(segment=segment)
        if isinstance(segment, Markdown):
            return self._format_markdown_segment(segment=segment)
        if isinstance(segment, LightApp):
            return self._format_light_app_segment(segment=segment)
        if isinstance(segment, MiniApp):
            return self._format_json_payload(
                label="小程序卡片",
                payload=segment.data.content,
            )
        if isinstance(segment, Node):
            return self._format_node_segment(segment=segment, depth=depth)
        if isinstance(segment, Face):
            return self._format_face_segment(segment=segment)
        if isinstance(segment, Dice):
            return "（骰子消息）"
        if isinstance(segment, Rps):
            return "（猜拳消息）"
        if isinstance(segment, Record):
            return self._format_record_segment(segment=segment)
        if isinstance(segment, Video):
            return self._format_video_segment(segment=segment)
        if isinstance(segment, Music):
            return self._format_music_segment(segment=segment)
        if isinstance(segment, Contact):
            return self._format_contact_segment(segment=segment)
        if isinstance(segment, Poke):
            return self._format_poke_segment(segment=segment)
        if isinstance(segment, Location):
            return self._format_location_segment(segment=segment)
        if isinstance(segment, Xml):
            return self._format_xml_segment(segment=segment)
        if isinstance(segment, UnknownSegment):
            return self._format_unknown_segment(segment=segment)
        return f"（暂不支持的消息段: {segment.type}）"

    def _format_at_segment(self, *, segment: At) -> str:
        """格式化艾特消息段。"""
        if segment.data.qq == "all":
            return "（艾特全体成员）"
        name = self._clean_text(segment.data.name)
        if name is not None:
            return f"（艾特: {name} ({segment.data.qq})）"
        return f"（艾特: {segment.data.qq}）"

    def _format_reply_segment(self, *, segment: Reply) -> str:
        """格式化回复消息段。"""
        return f"（回复消息，ID: {segment.data.id}）"

    def _format_image_segment(self, *, segment: Image, images_attached: bool) -> str:
        """格式化图片消息段。"""
        fields: list[str] = []
        self._append_optional_field(
            fields=fields,
            label="摘要",
            value=segment.data.summary,
        )
        self._append_optional_field(fields=fields, label="文件", value=segment.data.file)
        self._append_optional_field(
            fields=fields,
            label="文件 ID",
            value=segment.data.file_id,
        )
        self._append_optional_field(fields=fields, label="链接", value=segment.data.url)
        self._append_optional_field(
            fields=fields,
            label="大小",
            value=self._format_file_size(size=segment.data.file_size),
        )
        status = "图片内容已随本条输入提供" if images_attached else "未附带图片内容"
        fields.append(f"状态: {status}")
        return "（图片消息）\n" + "\n".join(fields)

    def _format_share_segment(self, *, segment: Share) -> str:
        """格式化链接分享消息段。"""
        fields: list[str] = []
        self._append_optional_field(fields=fields, label="标题", value=segment.data.title)
        self._append_optional_field(
            fields=fields, label="描述", value=segment.data.content
        )
        self._append_optional_field(fields=fields, label="链接", value=segment.data.url)
        self._append_optional_field(fields=fields, label="封面", value=segment.data.image)
        if not fields:
            return "（链接分享，未包含可读内容）"
        return "（链接分享）\n" + "\n".join(fields)

    def _format_forward_segment(self, *, segment: Forward, depth: int) -> str:
        """格式化合并转发消息段，优先展开已随事件携带的内容。"""
        header = f"（合并转发消息，ID: {segment.data.id}）"
        if segment.data.content is None:
            return (
                f"{header}\n"
                "（未包含可展开内容；如需阅读完整聊天记录，"
                f"应主动调用 qq__get_forward_message，参数 message_id=\"{segment.data.id}\"。）"
            )
        if depth >= FORWARD_MAX_DEPTH:
            return f"{header}\n（合并转发展开达到深度上限，剩余内容已省略）"
        content = self._format_forward_content(
            value=segment.data.content,
            depth=depth + 1,
        )
        return f"{header}\n{content}"

    def _format_forward_content(self, *, value: JsonValue, depth: int) -> str:
        """把合并转发的内嵌内容转换为文本。"""
        if depth > FORWARD_MAX_DEPTH:
            return "（合并转发展开达到深度上限，剩余内容已省略）"
        parsed_segments = self._try_parse_message_segments(value=value)
        if parsed_segments is not None:
            return self.format_segments(
                segments=parsed_segments,
                images_attached=False,
            )
        if isinstance(value, list):
            if not value:
                return "（合并转发内容为空）"
            formatted_items: list[str] = []
            visible_items = value[:FORWARD_MAX_ITEMS]
            for index, item in enumerate(visible_items, start=1):
                item_text = self._format_forward_item(value=item, depth=depth)
                formatted_items.append(f"{index}. {item_text}")
            remaining_count = len(value) - FORWARD_MAX_ITEMS
            if remaining_count > 0:
                formatted_items.append(
                    f"（其余 {remaining_count} 条合并转发内容已省略）"
                )
            return "\n".join(formatted_items)
        if isinstance(value, dict):
            return self._format_forward_item(value=value, depth=depth)
        if isinstance(value, str):
            return self._truncate_text(text=value, limit=FIELD_TEXT_LIMIT)
        return self._format_json_value(value=value, limit=FIELD_TEXT_LIMIT)

    def _format_forward_item(self, *, value: JsonValue, depth: int) -> str:
        """格式化合并转发中的单条消息或节点。"""
        parsed_segment = self._try_parse_message_segment(value=value)
        if parsed_segment is not None:
            return self.format_segments(
                segments=[parsed_segment],
                images_attached=False,
            )
        if isinstance(value, dict):
            sender = self._format_forward_sender(payload=value)
            content = self._extract_forward_item_content(payload=value)
            if content is not None:
                content_text = self._format_forward_content(
                    value=content,
                    depth=depth + 1,
                )
                if sender is not None:
                    return f"{sender}: {content_text}"
                return content_text
            return self._format_card_object(label="合并转发条目", payload=value)
        if isinstance(value, list):
            return self._format_forward_content(value=value, depth=depth + 1)
        if isinstance(value, str):
            return self._truncate_text(text=value, limit=FIELD_TEXT_LIMIT)
        return self._format_json_value(value=value, limit=FIELD_TEXT_LIMIT)

    def _format_node_segment(self, *, segment: Node, depth: int) -> str:
        """格式化合并转发节点消息段。"""
        sender = self._format_node_sender(segment=segment)
        if segment.data.content is None:
            if segment.data.id is not None:
                return f"（转发节点，ID: {segment.data.id}）"
            return "（转发节点，未包含可读内容）"
        if isinstance(segment.data.content, str):
            content = self._truncate_text(
                text=segment.data.content,
                limit=FIELD_TEXT_LIMIT,
            )
        else:
            content = self.format_segments(
                segments=segment.data.content,
                images_attached=False,
            )
        if sender is None:
            return f"（转发节点）\n{content}"
        return f"（转发节点，{sender}）\n{content}"

    def _format_json_payload(self, *, label: str, payload: JsonValue) -> str:
        """格式化 JSON 或卡片类负载。"""
        normalized_payload = self._normalize_card_payload(payload=payload)
        if isinstance(normalized_payload, dict):
            return self._format_card_object(label=label, payload=normalized_payload)
        if normalized_payload is None:
            return f"（{label}，未包含可读内容）"
        if isinstance(normalized_payload, list):
            summary = self._format_json_value(
                value=normalized_payload,
                limit=JSON_TEXT_LIMIT,
            )
            return f"（{label}）\n摘要: {summary}"
        text = self._truncate_text(
            text=str(normalized_payload),
            limit=FIELD_TEXT_LIMIT,
        )
        return f"（{label}）\n内容: {text}"

    def _format_card_object(self, *, label: str, payload: JsonObject) -> str:
        """从卡片对象中提取标题、描述和链接等可读字段。"""
        fields: list[str] = []
        title = self._find_first_text(
            value=payload,
            keys=("title", "name", "appName", "app_name", "app"),
        )
        description = self._find_first_text(
            value=payload,
            keys=("content", "desc", "description", "summary", "prompt", "text"),
        )
        url = self._find_first_text(
            value=payload,
            keys=("url", "jumpUrl", "jump_url", "link", "href"),
        )
        image = self._find_first_text(
            value=payload,
            keys=("image", "preview", "icon", "cover"),
        )
        self._append_optional_field(fields=fields, label="标题", value=title)
        self._append_optional_field(fields=fields, label="描述", value=description)
        self._append_optional_field(fields=fields, label="链接", value=url)
        self._append_optional_field(fields=fields, label="封面", value=image)
        if not fields:
            fields.append(
                "摘要: "
                + self._format_json_value(value=payload, limit=JSON_TEXT_LIMIT)
            )
        return f"（{label}）\n" + "\n".join(fields)

    def _format_mface_segment(self, *, segment: MFace) -> str:
        """格式化商城表情消息段。"""
        fields: list[str] = []
        self._append_optional_field(
            fields=fields, label="摘要", value=segment.data.summary
        )
        self._append_optional_field(
            fields=fields, label="表情 ID", value=segment.data.emoji_id
        )
        self._append_optional_field(
            fields=fields, label="表情包 ID", value=segment.data.emoji_package_id
        )
        self._append_optional_field(fields=fields, label="Key", value=segment.data.key)
        self._append_optional_field(fields=fields, label="链接", value=segment.data.url)
        if not fields:
            return "（商城表情，未包含可读内容）"
        return "（商城表情）\n" + "\n".join(fields)

    def _format_file_segment(self, *, segment: File) -> str:
        """格式化文件消息段。"""
        fields: list[str] = []
        file_name = self._clean_text(segment.data.name)
        if file_name is None:
            file_name = self._clean_text(segment.data.file)
        self._append_optional_field(fields=fields, label="文件名", value=file_name)
        self._append_optional_field(
            fields=fields, label="文件 ID", value=segment.data.file_id
        )
        self._append_optional_field(
            fields=fields,
            label="大小",
            value=self._format_file_size(size=segment.data.file_size),
        )
        if not fields:
            return "（文件消息，未包含可读内容）"
        return "（文件消息）\n" + "\n".join(fields)

    def _format_markdown_segment(self, *, segment: Markdown) -> str:
        """格式化 Markdown 消息段。"""
        content = self._clean_text(segment.data.content)
        if content is None:
            return "（Markdown 消息，内容为空）"
        return (
            "（Markdown 消息）\n"
            + self._truncate_text(text=content, limit=MARKDOWN_TEXT_LIMIT)
        )

    def _format_light_app_segment(self, *, segment: LightApp) -> str:
        """格式化小程序卡片消息段。"""
        payload: JsonObject = {}
        self._set_optional_payload_text(
            payload=payload, key="title", value=segment.data.title
        )
        description = segment.data.description
        if description is None:
            description = segment.data.desc
        self._set_optional_payload_text(
            payload=payload, key="description", value=description
        )
        self._set_optional_payload_text(payload=payload, key="url", value=segment.data.url)
        self._set_optional_payload_text(payload=payload, key="app", value=segment.data.app)
        nested_payload = segment.data.content
        if nested_payload is None:
            nested_payload = segment.data.data
        if nested_payload is None:
            return self._format_json_payload(label="小程序卡片", payload=payload)
        if not payload:
            return self._format_json_payload(label="小程序卡片", payload=nested_payload)
        payload["content"] = nested_payload
        return self._format_json_payload(label="小程序卡片", payload=payload)

    def _format_face_segment(self, *, segment: Face) -> str:
        """格式化系统表情消息段。"""
        fields: list[str] = [f"表情 ID: {segment.data.id}"]
        self._append_optional_field(
            fields=fields,
            label="结果 ID",
            value=segment.data.resultId,
        )
        if segment.data.chainCount is not None:
            fields.append(f"连发表情数量: {segment.data.chainCount}")
        if segment.data.raw is not None:
            fields.append(
                "原始摘要: "
                + self._format_json_value(value=segment.data.raw, limit=FIELD_TEXT_LIMIT)
            )
        return "（系统表情）\n" + "\n".join(fields)

    def _format_record_segment(self, *, segment: Record) -> str:
        """格式化语音消息段。"""
        fields = self._format_media_fields(
            file=segment.data.file,
            url=segment.data.url,
            file_id=segment.data.file_id,
            file_size=segment.data.file_size,
        )
        if segment.data.magic is not None:
            fields.append(f"变声: {'是' if segment.data.magic else '否'}")
        return "（语音消息）\n" + "\n".join(fields)

    def _format_video_segment(self, *, segment: Video) -> str:
        """格式化视频消息段。"""
        fields = self._format_media_fields(
            file=segment.data.file,
            url=segment.data.url,
            file_id=segment.data.file_id,
            file_size=segment.data.file_size,
        )
        return "（视频消息）\n" + "\n".join(fields)

    def _format_music_segment(self, *, segment: Music) -> str:
        """格式化音乐消息段。"""
        fields: list[str] = [f"类型: {segment.data.type}"]
        self._append_optional_field(fields=fields, label="标题", value=segment.data.title)
        self._append_optional_field(
            fields=fields,
            label="描述",
            value=segment.data.content,
        )
        self._append_optional_field(fields=fields, label="链接", value=segment.data.url)
        self._append_optional_field(fields=fields, label="音频", value=segment.data.audio)
        self._append_optional_field(fields=fields, label="封面", value=segment.data.image)
        self._append_optional_field(fields=fields, label="ID", value=segment.data.id)
        return "（音乐消息）\n" + "\n".join(fields)

    def _format_contact_segment(self, *, segment: Contact) -> str:
        """格式化推荐联系人或群聊消息段。"""
        contact_type = "群聊" if segment.data.type == "group" else "QQ 用户"
        return f"（推荐{contact_type}）\nID: {segment.data.id}"

    def _format_poke_segment(self, *, segment: Poke) -> str:
        """格式化戳一戳消息段。"""
        fields: list[str] = []
        self._append_optional_field(fields=fields, label="对象", value=segment.data.qq)
        self._append_optional_field(fields=fields, label="动作 ID", value=segment.data.id)
        self._append_optional_field(fields=fields, label="类型", value=segment.data.type)
        self._append_optional_field(fields=fields, label="名称", value=segment.data.name)
        if not fields:
            return "（戳一戳消息）"
        return "（戳一戳消息）\n" + "\n".join(fields)

    def _format_location_segment(self, *, segment: Location) -> str:
        """格式化位置消息段。"""
        fields: list[str] = [
            f"纬度: {segment.data.lat}",
            f"经度: {segment.data.lon}",
        ]
        self._append_optional_field(fields=fields, label="标题", value=segment.data.title)
        self._append_optional_field(
            fields=fields,
            label="描述",
            value=segment.data.content,
        )
        return "（位置消息）\n" + "\n".join(fields)

    def _format_xml_segment(self, *, segment: Xml) -> str:
        """格式化 XML 消息段。"""
        content = self._truncate_text(text=segment.data.data, limit=JSON_TEXT_LIMIT)
        return f"（XML 消息）\n内容: {content}"

    def _format_unknown_segment(self, *, segment: UnknownSegment) -> str:
        """格式化未显式支持的消息段，避免整段内容对 AI 不可见。"""
        if segment.data is None:
            return f"（暂不支持的消息段: {segment.type}）"
        content = self._format_json_value(value=segment.data, limit=JSON_TEXT_LIMIT)
        return f"（暂不支持的消息段: {segment.type}，内容: {content}）"

    def _format_media_fields(
        self,
        *,
        file: str,
        url: str | None,
        file_id: str | None,
        file_size: int | None,
    ) -> list[str]:
        """格式化音视频类消息的通用字段。"""
        fields: list[str] = []
        self._append_optional_field(fields=fields, label="文件", value=file)
        self._append_optional_field(fields=fields, label="文件 ID", value=file_id)
        self._append_optional_field(fields=fields, label="链接", value=url)
        self._append_optional_field(
            fields=fields,
            label="大小",
            value=self._format_file_size(size=file_size),
        )
        return fields

    def _normalize_card_payload(self, *, payload: JsonValue) -> JsonValue:
        """把 JSON 字符串卡片尽早收窄为项目内 JSON 值。"""
        if not isinstance(payload, str):
            return payload
        cleaned_payload = payload.strip()
        if cleaned_payload == "":
            return ""
        try:
            # json.loads 是动态协议边界，解析后立即收窄为项目内 JSON 值。
            parsed_payload = json.loads(cleaned_payload)
        except json.JSONDecodeError:
            return cleaned_payload
        try:
            return to_json_value(parsed_payload)
        except TypeError:
            return cleaned_payload

    def _try_parse_message_segments(
        self, *, value: JsonValue
    ) -> list[MessageSegment] | None:
        """尝试把原始 JSON 列表解析为消息段列表。"""
        if not isinstance(value, list):
            return None
        try:
            return self.segments_adapter.validate_python(value)
        except ValidationError:
            return None

    def _try_parse_message_segment(self, *, value: JsonValue) -> MessageSegment | None:
        """尝试把原始 JSON 对象解析为单个消息段。"""
        if not isinstance(value, dict) or "type" not in value:
            return None
        try:
            return self.segment_adapter.validate_python(value)
        except ValidationError:
            return None

    def _extract_forward_item_content(self, *, payload: JsonObject) -> JsonValue:
        """从合并转发条目中提取正文或消息段内容。"""
        for key in ("message", "content", "messages"):
            if key in payload and payload[key] is not None:
                return payload[key]
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("message", "content", "messages"):
                if key in data and data[key] is not None:
                    return data[key]
        return None

    def _format_forward_sender(self, *, payload: JsonObject) -> str | None:
        """格式化合并转发条目的发送者信息。"""
        sender = payload.get("sender")
        name: str | None = None
        user_id: str | None = None
        if isinstance(sender, dict):
            name = self._find_first_text(
                value=sender,
                keys=("card", "nickname", "name"),
            )
            user_id = self._find_first_text(value=sender, keys=("user_id", "uin", "qq"))
        if name is None:
            name = self._find_first_text(value=payload, keys=("nickname", "name"))
        if user_id is None:
            user_id = self._find_first_text(value=payload, keys=("user_id", "uin", "qq"))
        if name is not None and user_id is not None:
            return f"{name} ({user_id})"
        if name is not None:
            return name
        if user_id is not None:
            return user_id
        return None

    def _format_node_sender(self, *, segment: Node) -> str | None:
        """格式化转发节点中的发送者信息。"""
        name = self._clean_text(segment.data.nickname)
        user_id = segment.data.user_id
        if name is not None and user_id is not None:
            return f"{name} ({user_id})"
        if name is not None:
            return name
        return user_id

    def _find_first_text(
        self, *, value: JsonValue, keys: tuple[str, ...], depth: int = 0
    ) -> str | None:
        """在卡片 JSON 中按候选键递归寻找第一个可读文本。"""
        if depth > 3:
            return None
        if isinstance(value, dict):
            for key in keys:
                if key not in value:
                    continue
                text = self._json_scalar_to_text(value=value[key])
                if text is not None:
                    return text
            for item in value.values():
                text = self._find_first_text(value=item, keys=keys, depth=depth + 1)
                if text is not None:
                    return text
        if isinstance(value, list):
            for item in value:
                text = self._find_first_text(value=item, keys=keys, depth=depth + 1)
                if text is not None:
                    return text
        return None

    def _json_scalar_to_text(self, *, value: JsonValue) -> str | None:
        """把 JSON 标量转换为可展示文本。"""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (str, int, float)):
            return self._clean_text(str(value))
        return None

    def _append_optional_field(
        self, *, fields: list[str], label: str, value: str | None
    ) -> None:
        """把非空字段追加为统一的中文行。"""
        cleaned_value = self._clean_text(value)
        if cleaned_value is None:
            return
        fields.append(
            f"{label}: {self._truncate_text(text=cleaned_value, limit=FIELD_TEXT_LIMIT)}"
        )

    def _set_optional_payload_text(
        self, *, payload: JsonObject, key: str, value: str | None
    ) -> None:
        """把非空文本写入卡片摘要负载。"""
        cleaned_value = self._clean_text(value)
        if cleaned_value is not None:
            payload[key] = cleaned_value

    def _format_json_value(self, *, value: JsonValue, limit: int) -> str:
        """把 JSON 值压缩成一行摘要。"""
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return self._truncate_text(text=text, limit=limit)

    def _format_file_size(self, *, size: int | None) -> str | None:
        """把文件字节数格式化为用户可读大小。"""
        if size is None:
            return None
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

    def _truncate_text(self, *, text: str, limit: int) -> str:
        """按固定上限截断长文本并标记截断状态。"""
        cleaned_text = text.strip()
        if len(cleaned_text) <= limit:
            return cleaned_text
        return f"{cleaned_text[:limit]}...（已截断）"

    def _clean_text(self, value: str | None) -> str | None:
        """清理可选文本，空白文本统一视为缺失。"""
        if value is None:
            return None
        cleaned_value = value.strip()
        if cleaned_value == "":
            return None
        return cleaned_value
